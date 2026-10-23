#!/usr/bin/env python3
import json
import os
import re
import shutil
import smtplib
import subprocess
import sys
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter

from cockpit.macos import notify_macos
from cockpit.pipeline_monitor import audit_pipeline_health, update_pipeline_state

from pipeline_enrichment import (
    apply_row_highlights,
    build_action_items_sheet,
    build_developer_security_sheet,
    build_last_72_hours_sheet,
    enrich_dataframe,
    format_email_summary,
    load_previous_urls,
    save_current_urls,
    summarize_enrichment,
)

# Dropped from the emailed workbook (CSVs on disk still keep these for debugging).
COLUMNS_TO_DROP = ["description", "company_description", "job_url_direct"]
# Cap so one long URL doesn't stretch the sheet off-screen. Excel max is 255.
MAX_COLUMN_WIDTH = 80
MIN_COLUMN_WIDTH = 12


def sanitize_text(text):
    if isinstance(text, str):
        # Remove control characters that Excel doesn't allow in XML
        # Keep \n (\x0a), \t (\x09), and \r (\x0d)
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text


def is_http_url(value) -> bool:
    return isinstance(value, str) and value.strip().startswith(("http://", "https://"))


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Drop bulky/unwanted columns and strip Excel-illegal control characters."""
    df = df.fillna("")
    df = df.drop(
        columns=[col for col in COLUMNS_TO_DROP if col in df.columns],
        errors="ignore",
    )
    for col in df.select_dtypes(include=["object"]):
        df[col] = df[col].apply(lambda x: sanitize_text(x) if isinstance(x, str) else x)
    return df


def apply_hyperlinks(ws) -> None:
    """Turn every http(s) cell into a clickable Excel hyperlink."""
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            val = cell.value
            if not is_http_url(val):
                continue
            url = val.strip()
            cell.value = url
            cell.hyperlink = url
            cell.style = "Hyperlink"


def autosize_columns(ws) -> None:
    """Expand every column to fit its header and cell values, within a usable cap."""
    for col_idx in range(1, ws.max_column + 1):
        letter = get_column_letter(col_idx)
        max_length = 0
        for cell in ws[letter]:
            if cell.value is None:
                continue
            text = str(cell.value).split("\n", 1)[0]
            max_length = max(max_length, len(text))
        width = min(max(max_length + 2, MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH)
        ws.column_dimensions[letter].width = width


def format_worksheet(ws, df: pd.DataFrame | None = None) -> None:
    apply_hyperlinks(ws)
    autosize_columns(ws)
    if df is not None:
        apply_row_highlights(ws, df)


# Define paths
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "pipeline_config.json"
STATE_FILE = BASE_DIR / ".pipeline_state" / "previous_job_urls.json"

EXPECTED_OUTPUTS = {
    "all_city_remote_jobs.csv": "Remote Jobs",
    "seattle_jobs.csv": "Seattle Jobs",
    "seattle_entry_level_jobs.csv": "Seattle Entry Level",
    "west_coast_jobs.csv": "West Coast Jobs",
    "seattle_msba_jobs.csv": "Seattle MSBA Jobs",
    "west_coast_msba_jobs.csv": "West Coast MSBA Jobs",
    "ats_direct_jobs.csv": "ATS Direct Jobs",
    "tech_portals_jobs.csv": "Tech Portals Jobs",
}

EMAIL_PLACEHOLDERS = {
    "ENTER_SENDER_EMAIL_HERE",
    "ENTER_SENDER_APP_PASSWORD_HERE",
    "ENTER_RECEIVER_EMAIL_HERE",
    "YOUR_EMAIL@gmail.com",
    "YOUR_16_CHAR_APP_PASSWORD",
    "RECIPIENT_EMAIL@gmail.com",
}


def is_email_configured(config: dict) -> bool:
    if config.get("email_enabled") is False:
        return False
    sender_email = config.get("sender_email")
    sender_password = config.get("sender_password")
    receiver_email = config.get("receiver_email") or config.get("recipient_email")
    if not sender_email or sender_email in EMAIL_PLACEHOLDERS:
        return False
    if not sender_password or sender_password in EMAIL_PLACEHOLDERS:
        return False
    if not receiver_email or receiver_email in EMAIL_PLACEHOLDERS:
        return False
    return True


def load_config():
    if not CONFIG_FILE.exists():
        default_config = {
            "email_enabled": False,
            "sender_email": "ENTER_SENDER_EMAIL_HERE",
            "sender_password": "ENTER_SENDER_APP_PASSWORD_HERE",
            "receiver_email": "ENTER_RECEIVER_EMAIL_HERE",
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        print(f"[!] Created default configuration file: {CONFIG_FILE}")
        print("[!] Email delivery is disabled by default; scraping and Cockpit ingestion will proceed.")
        return default_config

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def run_scrapers():
    print(f"\n[{datetime.now()}] Step 1: Spawning parallel scraper scripts...")
    runner_script = BASE_DIR / "run_parallel.py"

    if not runner_script.exists():
        raise RuntimeError(f"Scraper parallel runner not found at: {runner_script}")

    py_bin = os.environ.get("JOBSPY_PYTHON") or sys.executable
    # Execute the scraper parallel runner using the current Python environment
    result = subprocess.run([py_bin, str(runner_script)], cwd=str(BASE_DIR), env=os.environ.copy())
    if result.returncode != 0:
        raise RuntimeError(
            f"Scraper parallel runner failed with return code {result.returncode}"
        )
    print("Scraping completed successfully.")

def validate_config(config):
    if not is_email_configured(config):
        print(
            f"[!] Email delivery is not configured in '{CONFIG_FILE.name}' — "
            "Step 3 (Email) will be skipped after scraping and Excel build."
        )
        return False
    return True

def build_excel_workbook(results_dir, expected_outputs, state_file=STATE_FILE):
    timestamp_day = datetime.now().strftime("%Y_%m_%d")
    excel_filename = f"Job_Report_{timestamp_day}.xlsx"
    excel_path = results_dir / excel_filename

    print(f"\n[{datetime.now()}] Step 2: Compiling CSV files into Excel workbook...")
    previous_urls_by_segment = load_previous_urls(state_file)
    has_previous_run = bool(previous_urls_by_segment)
    segment_frames: dict[str, pd.DataFrame] = {}
    current_urls_by_segment: dict[str, list[str]] = {}
    prepared_tabs: list[tuple[str, str, pd.DataFrame, pd.DataFrame]] = []

    try:
        has_data = False
        for csv_name, sheet_tab in expected_outputs.items():
            csv_path = results_dir / csv_name
            if not csv_path.exists():
                print(f" - {csv_name}: File not found. Skipping tab '{sheet_tab}'.")
                continue

            print(f" - Processing '{sheet_tab}' from '{csv_name}'...")
            raw_df = pd.read_csv(csv_path)
            if "job_url" in raw_df.columns:
                current_urls_by_segment[csv_name] = raw_df["job_url"].astype(str).tolist()

            df = enrich_dataframe(
                raw_df,
                previous_urls_by_segment.get(csv_name, set()),
                has_previous_run=has_previous_run,
            )
            segment_frames[sheet_tab] = df
            df_prepared = prepare_dataframe(df)

            if "title" in df_prepared.columns:
                df_prepared = df_prepared.sort_values(
                    by="title",
                    key=lambda col: col.astype(str).str.lower(),
                    ascending=True,
                    na_position="last",
                ).reset_index(drop=True)

            pattern = r"\b(?:analyst|analytics|data)\b"
            if "title" in df_prepared.columns:
                mask = df_prepared["title"].astype(str).str.contains(pattern, flags=re.IGNORECASE, regex=True, na=False)
                filtered_df = df_prepared[mask].reset_index(drop=True)
            else:
                filtered_df = pd.DataFrame(columns=df_prepared.columns)

            prepared_tabs.append((sheet_tab, f"{sheet_tab} (Filtered)", df_prepared, filtered_df))
            has_data = True

        if not has_data:
            raise RuntimeError("No CSV data was found to compile into Excel")

        action_items = build_action_items_sheet(segment_frames)
        action_items_prepared = prepare_dataframe(action_items) if not action_items.empty else action_items

        fresh_jobs = build_last_72_hours_sheet(segment_frames)
        fresh_jobs_prepared = prepare_dataframe(fresh_jobs) if not fresh_jobs.empty else fresh_jobs

        dev_sec_items = build_developer_security_sheet(segment_frames)
        dev_sec_prepared = prepare_dataframe(dev_sec_items) if not dev_sec_items.empty else dev_sec_items

        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            print(" - Adding tab 'Last 72 Hours'...")
            fresh_jobs_prepared.to_excel(writer, sheet_name="Last 72 Hours", index=False)
            format_worksheet(writer.sheets["Last 72 Hours"], fresh_jobs_prepared)

            # Tab 1: Front-load Action Items
            print(f" - Adding tab 'Action Items' (Fresher Spotlight)...")
            action_items_prepared.to_excel(writer, sheet_name="Action Items", index=False)
            if not action_items_prepared.empty:
                format_worksheet(writer.sheets["Action Items"], action_items_prepared)
            print(f"   -> Action Items tab: {len(action_items_prepared)} prioritized listings")

            # Tab 2: Developer & Security Roles
            print(f" - Adding tab 'Developer & Security'...")
            dev_sec_prepared.to_excel(writer, sheet_name="Developer & Security", index=False)
            if not dev_sec_prepared.empty:
                format_worksheet(writer.sheets["Developer & Security"], dev_sec_prepared)
            print(f"   -> Developer & Security tab: {len(dev_sec_prepared)} prioritized listings")

            # Subsequent tabs: raw segment tabs and filtered tabs
            for sheet_tab, filtered_sheet_tab, df_prepared, filtered_df in prepared_tabs:
                print(f" - Adding tab '{sheet_tab}'...")
                df_prepared.to_excel(writer, sheet_name=sheet_tab, index=False)
                filtered_df.to_excel(writer, sheet_name=filtered_sheet_tab, index=False)

                for target_tab, tab_df in [(sheet_tab, df_prepared), (filtered_sheet_tab, filtered_df)]:
                    format_worksheet(writer.sheets[target_tab], tab_df)

                new_count = int(df_prepared["is_new"].sum()) if "is_new" in df_prepared.columns else 0
                fresh_count = int(df_prepared["is_fresh_post"].sum()) if "is_fresh_post" in df_prepared.columns else 0
                print(f"   -> {new_count} new, {fresh_count} fresh posts, apply types added")

            # Reorder safety check using openpyxl
            desired_front_tabs = ["Last 72 Hours", "Action Items", "Developer & Security"]
            for target_idx, tab_name in enumerate(desired_front_tabs):
                if tab_name in writer.book.sheetnames:
                    current_idx = writer.book.sheetnames.index(tab_name)
                    if current_idx != target_idx:
                        sheet_obj = writer.book._sheets.pop(current_idx)
                        writer.book._sheets.insert(target_idx, sheet_obj)

        if current_urls_by_segment:
            save_current_urls(state_file, current_urls_by_segment)

        size_mb = excel_path.stat().st_size / (1024 * 1024)
        print(f"Excel workbook created successfully: {excel_path} ({size_mb:.2f} MB)")
        stats = summarize_enrichment(segment_frames)
        return excel_path, stats, has_previous_run

    except Exception as e:
        raise RuntimeError(f"Failed to compile Excel workbook: {e}") from e

def send_email_with_attachment(config, excel_path, stats, has_previous_run):
    if not is_email_configured(config):
        print(f"\n[{datetime.now()}] Step 3: Skipping email delivery (SMTP not configured).")
        return False

    sender_email = config.get("sender_email")
    sender_password = config.get("sender_password")
    receiver_email = config.get("receiver_email") or config.get("recipient_email")
    smtp_server = config.get("smtp_server", "smtp.gmail.com")
    smtp_port = config.get("smtp_port", 587)

    print(f"\n[{datetime.now()}] Step 3: Preparing email attachment...")

    # Create the message container
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"Daily Job Scraper Report - {datetime.now().strftime('%Y-%m-%d')}"

    body = (
        f"Hello,\n\n"
        f"Please find attached the consolidated job scraper report for {datetime.now().strftime('%Y-%m-%d')}.\n\n"
        f"{format_email_summary(stats, has_previous_run=has_previous_run)}"
        f"Start with the Action Items and Developer & Security tabs — curated early-career roles sorted by priority.\n\n"
        f"Best regards,\n"
        f"Career Pipeline"
    )
    msg.attach(MIMEText(body, 'plain'))

    # Open the Excel file in binary mode
    try:
        with open(excel_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())

        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename= {excel_path.name}",
        )
        msg.attach(part)

        # Connect to SMTP server and send
        print(f"Connecting to SMTP server {smtp_server}:{smtp_port}...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()  # Secure the connection
        server.login(sender_email, sender_password)

        print("Sending email...")
        text = msg.as_string()
        server.sendmail(sender_email, receiver_email, text)
        server.quit()
        print("Email sent successfully!")

    except Exception as e:
        raise RuntimeError(f"Failed to send email: {e}") from e

def run_cockpit_ingest(results_dir: Path | None = None) -> bool:
    print(f"\n[{datetime.now()}] Step 4: Running Cockpit Ingest...")
    reqs_path = BASE_DIR / "cockpit" / "requirements.txt"
    uv_bin = shutil.which("uv") or "/opt/homebrew/bin/uv"

    if Path(uv_bin).is_file() and reqs_path.is_file():
        cmd = [
            str(uv_bin),
            "run",
            "--no-project",
            "--python",
            "3.12",
            "--with-requirements",
            str(reqs_path),
            "python",
            "-m",
            "cockpit.cli",
            "ingest",
        ]
    else:
        cmd = [sys.executable, "-m", "cockpit.cli", "ingest"]

    if results_dir is not None:
        csv_files = sorted(results_dir.glob("*_jobs.csv"))
        if not csv_files:
            print("[!] No current CSVs available for Cockpit ingest")
            return False
        cmd.extend(str(p) for p in csv_files)

    try:
        res = subprocess.run(cmd, cwd=str(BASE_DIR))
        if res.returncode != 0:
            print(f"[!] Warning: Cockpit ingest exited with code {res.returncode}")
            return False
        print("Cockpit ingest completed successfully.")
        return True
    except Exception as exc:
        print(f"[!] Warning: Failed to run Cockpit ingest: {exc}")
        return False

def main():
    config = load_config()
    validate_config(config)

    # List of expected CSV outputs mapping to target Excel tabs
    expected_outputs = EXPECTED_OUTPUTS

    # Locate scraped jobs results directory organized by date within month
    timestamp_month = datetime.now().strftime("%Y_%m")
    timestamp_day = datetime.now().strftime("%Y_%m_%d")
    results_dir = BASE_DIR / "jobspy" / "scraped jobs" / "results" / timestamp_month / timestamp_day

    # Check if results folder exists or create it
    results_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JOBSPY_RESULTS_DIR"] = str(results_dir)

    logs_dir = BASE_DIR / "jobspy" / "scraped jobs" / "logs" / timestamp_month / timestamp_day
    logs_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JOBSPY_LOGS_DIR"] = str(logs_dir)

    update_pipeline_state(
        BASE_DIR,
        pid=os.getpid(),
        started_at=datetime.now().isoformat(),
        phase="SCRAPING",
        error=None,
        tasks={},
        finished_at=None,
        excel_path=None,
        health="HEALTHY",
        anomalies=[],
    )

    try:
        run_scrapers()

        update_pipeline_state(BASE_DIR, phase="EXCEL_BUILD")
        excel_path, stats, has_previous_run = build_excel_workbook(results_dir, expected_outputs)
        update_pipeline_state(BASE_DIR, excel_path=str(excel_path))

        email_error = None
        try:
            update_pipeline_state(BASE_DIR, phase="EMAIL")
            send_email_with_attachment(config, excel_path, stats, has_previous_run)
        except Exception as exc:
            email_error = exc
        finally:
            update_pipeline_state(BASE_DIR, phase="INGEST")
            if not run_cockpit_ingest(results_dir):
                message = "Cockpit ingest failed"
                if email_error is not None:
                    message = f"{email_error}; {message}"
                raise RuntimeError(message) from email_error
        if email_error is not None:
            raise email_error

        update_pipeline_state(
            BASE_DIR,
            phase="COMPLETED",
            finished_at=datetime.now().isoformat(),
        )
        print(f"\n[{datetime.now()}] --- PIPELINE RUN COMPLETE ---")

        # Health audit and macOS notification
        health_report = audit_pipeline_health(BASE_DIR, results_dir)
        total_scraped = sum(stats.get("raw_counts", {}).values()) if "stats" in locals() and stats else 0
        anomalies = health_report.get("anomalies", [])

        if health_report.get("health") == "HEALTHY":
            notify_macos(
                title="Career Pipeline Complete",
                message=f"Pipeline complete — {total_scraped} jobs scraped across 8 segments",
            )
        else:
            notify_macos(
                title="Pipeline Warning",
                message=f"{len(anomalies)} anomalies detected (check cockpit pipeline)",
            )
    except Exception as exc:
        update_pipeline_state(
            BASE_DIR,
            phase="FAILED",
            error=str(exc),
            finished_at=datetime.now().isoformat(),
        )
        notify_macos(
            title="Pipeline Failed",
            message=str(exc)[:120],
        )
        raise

if __name__ == "__main__":
    main()
