# Task 3 Brief: Enhance Apply Type Classification (`pipeline_enrichment.py`)

## Objective
Update `classify_apply_type` in `/Users/alex/Github/n1ecC/Jobspy-Custom/pipeline_enrichment.py` to identify direct ATS providers from job URLs and handle `site == "ats"`.

## Target File
- File: `/Users/alex/Github/n1ecC/Jobspy-Custom/pipeline_enrichment.py`
- Function: `classify_apply_type(row: pd.Series) -> str`

## Requirements
1. In `classify_apply_type(row: pd.Series) -> str`:
   Check both `job_url` and `job_url_direct` for ATS hostnames before checking generic site fallbacks:
   - Contains `"greenhouse.io"` -> return `"Greenhouse Direct"`
   - Contains `"lever.co"` -> return `"Lever Direct"`
   - Contains `"ashbyhq.com"` -> return `"Ashby Direct"`
   - Contains `"myworkdayjobs.com"` or `"workday"` in URL -> return `"Workday Direct"`
   - Contains `"smartrecruiters.com"` -> return `"SmartRecruiters Direct"`
   - If `site == "ats"` -> return `"Company ATS Direct"`
2. Preserve existing logic for Indeed, LinkedIn, ZipRecruiter, Glassdoor, Google Jobs, and generic Company Site.
3. Test with unit assertions:
   ```python
   import pandas as pd
   from pipeline_enrichment import classify_apply_type

   assert classify_apply_type(pd.Series({'job_url': 'https://boards.greenhouse.io/stripe/jobs/123'})) == 'Greenhouse Direct'
   assert classify_apply_type(pd.Series({'job_url': 'https://jobs.lever.co/spotify/456'})) == 'Lever Direct'
   assert classify_apply_type(pd.Series({'job_url': 'https://jobs.ashbyhq.com/openai/789'})) == 'Ashby Direct'
   assert classify_apply_type(pd.Series({'job_url': 'https://salesforce.wd12.myworkdayjobs.com/en-US/jobs/1'})) == 'Workday Direct'
   assert classify_apply_type(pd.Series({'site': 'ats', 'job_url': 'https://custom.com/careers'})) == 'Company ATS Direct'
   ```
4. Commit: `git add pipeline_enrichment.py && git commit -m "feat(enrichment): add ATS apply type classification"`
5. Write report to `/Users/alex/Github/n1ecC/Jobspy-Custom/.superpowers/sdd/2026-09-02-ats-scrapers-integration/task-3-report.md`.
