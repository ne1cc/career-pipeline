#!/usr/bin/env python3
import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
import pandas as pd
import re

def sanitize_text(text):
    if isinstance(text, str):
        # Remove control characters that Excel doesn't allow in XML
        # Keep \n (\x0a), \t (\x09), and \r (\x0d)
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

# Define paths
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "pipeline_config.json"

def load_config():
    if not CONFIG_FILE.exists():
        default_config = {
            "sender_email": "ENTER_SENDER_EMAIL_HERE",
            "sender_password": "ENTER_SENDER_APP_PASSWORD_HERE",
            "receiver_email": "ENTER_RECEIVER_EMAIL_HERE",
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        print(f"[!] Created default configuration file: {CONFIG_FILE}")
        print("Please edit it and set your Email settings before running.")
        sys.exit(0)
        
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def run_scrapers():
    print(f"\n[{datetime.now()}] Step 1: Spawning parallel scraper scripts...")
    runner_script = BASE_DIR / "run_parallel.py"
    
    if not runner_script.exists():
        print(f"ERROR: Scraper parallel runner not found at: {runner_script}")
        sys.exit(1)
        
    # Execute the scraper parallel runner using the current Python environment
    result = subprocess.run([sys.executable, str(runner_script)], cwd=str(BASE_DIR))
    if result.returncode != 0:
        print(f"ERROR: Scraper parallel runner failed with return code {result.returncode}!")
        sys.exit(result.returncode)
    print("Scraping completed successfully.")

def validate_config(config):
    sender_email = config.get("sender_email")
    sender_password = config.get("sender_password")
    receiver_email = config.get("receiver_email")
    
    placeholders = ["ENTER_SENDER_EMAIL_HERE", "ENTER_SENDER_APP_PASSWORD_HERE", "ENTER_RECEIVER_EMAIL_HERE"]
    
    if not sender_email or sender_email in placeholders:
        print(f"ERROR: Sender email is not configured in '{CONFIG_FILE.name}'!")
        sys.exit(1)
    if not sender_password or sender_password in placeholders:
        print(f"ERROR: Sender password is not configured in '{CONFIG_FILE.name}'!")
        sys.exit(1)
    if not receiver_email or receiver_email in placeholders:
        print(f"ERROR: Receiver email is not configured in '{CONFIG_FILE.name}'!")
        sys.exit(1)

def build_excel_workbook(results_dir, expected_outputs):
    timestamp_day = datetime.now().strftime("%Y_%m_%d")
    excel_filename = f"Job_Report_{timestamp_day}.xlsx"
    excel_path = results_dir / excel_filename
    
    print(f"\n[{datetime.now()}] Step 2: Compiling CSV files into Excel workbook...")
    
    try:
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            has_data = False
            for csv_name, sheet_tab in expected_outputs.items():
                csv_path = results_dir / csv_name
                if not csv_path.exists():
                    print(f" - {csv_name}: File not found. Skipping tab '{sheet_tab}'.")
                    continue
                
                print(f" - Adding tab '{sheet_tab}' from '{csv_name}'...")
                df = pd.read_csv(csv_path)
                df = df.fillna("")
                
                # Drop large text columns to keep Excel files small and emailable
                columns_to_drop = ["description", "company_description"]
                df = df.drop(columns=[col for col in columns_to_drop if col in df.columns], errors="ignore")
                
                # Sanitize all string columns to prevent openpyxl IllegalCharacterError
                for col in df.select_dtypes(include=['object']):
                    df[col] = df[col].apply(lambda x: sanitize_text(x) if isinstance(x, str) else x)
                    
                # Filter DataFrame for target keywords: Analyst, Analytics, or Data
                pattern = r"\b(?:analyst|analytics|data)\b"
                if "title" in df.columns:
                    mask = df["title"].astype(str).str.contains(pattern, flags=re.IGNORECASE, regex=True, na=False)
                    filtered_df = df[mask]
                else:
                    filtered_df = pd.DataFrame(columns=df.columns)
                
                # Write main raw sheet and filtered sheet
                df.to_excel(writer, sheet_name=sheet_tab, index=False)
                filtered_sheet_tab = f"{sheet_tab} (Filtered)"
                filtered_df.to_excel(writer, sheet_name=filtered_sheet_tab, index=False)
                
                # Format URL columns as clickable Excel hyperlinks for both sheets
                for target_tab in [sheet_tab, filtered_sheet_tab]:
                    ws = writer.sheets[target_tab]
                    headers = [cell.value for cell in ws[1]]
                    url_col_indices = [
                        idx + 1 for idx, header in enumerate(headers)
                        if header and isinstance(header, str) and ("url" in header.lower() or "link" in header.lower())
                    ]
                    
                    for col_idx in url_col_indices:
                        for row_idx in range(2, ws.max_row + 1):
                            cell = ws.cell(row=row_idx, column=col_idx)
                            val = cell.value
                            if val and isinstance(val, str) and (val.startswith("http://") or val.startswith("https://")):
                                cell.hyperlink = val
                                cell.style = "Hyperlink"
                
                has_data = True
                
        if not has_data:
            print("ERROR: No CSV files were found to compile into Excel!")
            sys.exit(1)
            
        size_mb = excel_path.stat().st_size / (1024 * 1024)
        print(f"Excel workbook created successfully: {excel_path} ({size_mb:.2f} MB)")
        return excel_path
        
    except Exception as e:
        print(f"ERROR: Failed to compile Excel workbook: {e}")
        sys.exit(1)

def send_email_with_attachment(config, excel_path):
    sender_email = config.get("sender_email")
    sender_password = config.get("sender_password")
    receiver_email = config.get("receiver_email")
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
        f"This report contains segments for remote jobs, seattle area, entry level, and west coast jobs.\n\n"
        f"Best regards,\n"
        f"Jobspy Custom Pipeline"
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
        print(f"ERROR: Failed to send email: {e}")
        sys.exit(1)

def main():
    config = load_config()
    validate_config(config)
    
    # List of expected CSV outputs mapping to target Excel tabs
    expected_outputs = {
        "all_city_remote_jobs.csv": "Remote Jobs",
        "seattle_jobs.csv": "Seattle Jobs",
        "seattle_entry_level_jobs.csv": "Seattle Entry Level",
        "west_coast_jobs.csv": "West Coast Jobs",
    }
    
    # Locate scraped jobs results directory
    timestamp_month = datetime.now().strftime("%Y_%m")
    results_dir = BASE_DIR / "jobspy" / "scraped jobs" / "results" / timestamp_month
    
    # Check if results folder exists or create it
    results_dir.mkdir(parents=True, exist_ok=True)
    
    run_scrapers()
    
    excel_path = build_excel_workbook(results_dir, expected_outputs)
    
    send_email_with_attachment(config, excel_path)
    
    print(f"\n[{datetime.now()}] --- PIPELINE RUN COMPLETE ---")

if __name__ == "__main__":
    main()
