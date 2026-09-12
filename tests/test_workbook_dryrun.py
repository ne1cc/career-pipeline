from pathlib import Path
import pandas as pd
import openpyxl
from run_pipeline import build_excel_workbook, EXPECTED_OUTPUTS
from pipeline_enrichment import format_email_summary, summarize_enrichment


def test_build_excel_workbook_end_to_end(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    # Create sample CSVs for all 8 expected outputs
    sample_jobs = [
        {"title": "Junior Data Analyst", "company": "Co A", "job_url": "https://a.com/job1", "date_posted": "2026-09-10", "site": "linkedin"},
        {"title": "Senior Data Scientist", "company": "Co B", "job_url": "https://b.com/job2", "date_posted": "2026-09-08", "site": "indeed"},
        {"title": "Analytics Engineer", "company": "Co C", "job_url": "https://c.com/job3", "date_posted": "2026-09-11", "site": "ats"},
    ]
    df = pd.DataFrame(sample_jobs)
    for csv_name in EXPECTED_OUTPUTS.keys():
        df.to_csv(results_dir / csv_name, index=False)

    state_file = tmp_path / "state.json"
    excel_path, stats, has_previous_run = build_excel_workbook(results_dir, EXPECTED_OUTPUTS, state_file=state_file)
    assert excel_path.exists()
    assert stats["total_jobs"] == len(EXPECTED_OUTPUTS) * len(sample_jobs)
    assert stats["action_items"] == 2
    assert stats["direct_ats"] == len(EXPECTED_OUTPUTS)

    wb = openpyxl.load_workbook(excel_path)
    sheet_names = wb.sheetnames

    # Verify Tab 1 is Action Items
    assert sheet_names[0] == "Action Items"

    # Verify all raw tabs exist
    for target_tab in EXPECTED_OUTPUTS.values():
        assert target_tab in sheet_names

    # Verify Action Items excluded the Senior role
    ws_action = wb["Action Items"]
    header = [cell.value for cell in ws_action[1]]
    title_idx = header.index("title")
    all_action_titles = [row[title_idx] for row in ws_action.iter_rows(min_row=2, values_only=True)]
    assert "Senior Data Scientist" not in all_action_titles
    assert "Analytics Engineer" in all_action_titles
    all_text = " ".join([str(val) for row in ws_action.iter_rows(values_only=True) for val in row if val])
    assert "Senior Data Scientist" not in all_text
    assert "Analytics Engineer" in all_text


def test_email_summary_highlights():
    sample_df = pd.DataFrame([
        {"title": "Junior Data Analyst", "company": "Co A", "job_url": "https://a.com/job1", "date_posted": "2026-09-10", "site": "ats", "apply_type": "Company ATS Direct", "is_new": True, "is_fresh_post": True},
        {"title": "Analytics Engineer", "company": "Co B", "job_url": "https://b.com/job2", "date_posted": "2026-09-11", "site": "linkedin", "apply_type": "LinkedIn Easy Apply", "is_new": True, "is_fresh_post": True},
    ])
    segment_frames = {"Test Segment": sample_df}
    stats = summarize_enrichment(segment_frames)
    summary = format_email_summary(stats, has_previous_run=True)

    assert "Total Raw Postings" in summary
    assert "Action Items (Fresher Spotlight)" in summary
    assert "Direct ATS" in summary
