# Task 4 Brief: Integrate ATS Scraper into Parallel Runner (`run_parallel.py`)

## Objective
Update `run_parallel.py` in `/Users/alex/Github/n1ecC/Jobspy-Custom` to execute `jobspy/search_jobs_ats.py` concurrently with the other scraper scripts and track `ats_direct_jobs.csv`.

## Target File
- File: `/Users/alex/Github/n1ecC/Jobspy-Custom/run_parallel.py`

## Requirements
1. In `run_parallel.py`, add `jobspy_dir / "search_jobs_ats.py"` to the `scripts` list:
   ```python
   scripts = [
       jobspy_dir / "search_jobs_remote.py",
       jobspy_dir / "search_jobs_seattle.py",
       jobspy_dir / "search_jobs_seattle_entry_level.py",
       jobspy_dir / "search_jobs_west.py",
       jobspy_dir / "search_jobs_ats.py",
   ]
   ```
2. In `run_parallel.py`, update `expected_outputs` dictionary to include `ats_direct_jobs.csv`:
   ```python
   expected_outputs = {
       "search_jobs_remote.py": "all_city_remote_jobs.csv",
       "search_jobs_seattle.py": "seattle_jobs.csv",
       "search_jobs_seattle_entry_level.py": "seattle_entry_level_jobs.csv",
       "search_jobs_west.py": "west_coast_jobs.csv",
       "search_jobs_ats.py": "ats_direct_jobs.csv",
   }
   ```
3. Test script validation:
   Verify that running Python import / script check works:
   `poetry run python -c "import run_parallel; print('run_parallel valid')"`
4. Commit: `git add run_parallel.py && git commit -m "feat(runner): add search_jobs_ats.py to parallel runner"`
5. Write report to `/Users/alex/Github/n1ecC/Jobspy-Custom/.superpowers/sdd/2026-09-02-ats-scrapers-integration/task-4-report.md`.
