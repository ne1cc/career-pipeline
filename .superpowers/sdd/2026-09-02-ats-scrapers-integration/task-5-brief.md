# Task 5 Brief: Integrate ATS Jobs into Daily Pipeline & Excel Workbook (`run_pipeline.py`)

## Objective
Update `run_pipeline.py` in `/Users/alex/Github/n1ecC/Jobspy-Custom` so that `ats_direct_jobs.csv` is compiled into the daily Excel report as:
1. `ATS Direct Jobs` tab
2. `ATS Direct Jobs (Filtered)` tab (filtered to Analyst/Data/Analytics roles)
And contributes to the `Action Items` tab and email summary metrics.

## Target File
- File: `/Users/alex/Github/n1ecC/Jobspy-Custom/run_pipeline.py`

## Requirements
1. In `run_pipeline.py` inside `main()` (lines ~288-297), update `expected_outputs` dictionary to add `"ats_direct_jobs.csv": "ATS Direct Jobs"`:
   ```python
   expected_outputs = {
       "all_city_remote_jobs.csv": "Remote Jobs",
       "seattle_jobs.csv": "Seattle Jobs",
       "seattle_entry_level_jobs.csv": "Seattle Entry Level",
       "west_coast_jobs.csv": "West Coast Jobs",
       "ats_direct_jobs.csv": "ATS Direct Jobs",
   }
   ```
2. Test Excel compilation function with the existing `ats_direct_jobs.csv`:
   ```bash
   poetry run python -c "
   from pathlib import Path
   from datetime import datetime
   import openpyxl
   import run_pipeline

   results_dir = Path('jobspy/scraped jobs/results') / datetime.now().strftime('%Y_%m')
   expected = {'ats_direct_jobs.csv': 'ATS Direct Jobs'}
   excel_path, stats, has_prev = run_pipeline.build_excel_workbook(results_dir, expected)
   wb = openpyxl.load_workbook(excel_path, read_only=True)
   sheet_names = wb.sheet_names
   print('Generated sheets:', sheet_names)
   assert 'ATS Direct Jobs' in sheet_names
   assert 'ATS Direct Jobs (Filtered)' in sheet_names
   assert 'Action Items' in sheet_names
   print('Excel workbook verification passed!')
   "
   ```
3. Commit: `git add run_pipeline.py && git commit -m "feat(pipeline): include ATS Direct Jobs in daily Excel workbook"`
4. Write report to `/Users/alex/Github/n1ecC/Jobspy-Custom/.superpowers/sdd/2026-09-02-ats-scrapers-integration/task-5-report.md`.
