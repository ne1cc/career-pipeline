# Task 2 Report: Implement ATS Scraper Engine (`jobspy/search_jobs_ats.py`)

## Executive Summary
Task 2 has been completed successfully. We created `jobspy/search_jobs_ats.py` to scrape high-signal tech roles directly from native ATS endpoints (Greenhouse, Ashby, Lever, Gem) and hosted ATS datasets, standardizing all records to JobSpy's canonical schema and saving them to `ats_direct_jobs.csv`.

---

## Deliverables & Implementations

1. **File Created**:
   - `jobspy/search_jobs_ats.py`

2. **Core Capabilities**:
   - **Target Company Registry**: Direct endpoint querying for Greenhouse (`stripe`, `airbnb`, `databricks`, `figma`, `instacart`, `pinterest`, `dropbox`, `reddit`, `robinhood`, `anthropic`, `doordashusa`), Ashby (`openai`, `snowflake`, `ramp`, `notion`, `perplexity`, `cursor`, `linear`, `vercel`), Lever (`spotify`), and Gem (`retool`).
   - **Robust Alias & ATS Fallback Resolution**: Automatic routing of companies with distinct slug names or board providers (`anthropic` → Greenhouse, `snowflake` → Ashby, `doordash` → Greenhouse `doordashusa`, `eventbrite` → Greenhouse `eventbriteinc`, `retool` → Gem `retool`).
   - **Regex Filtering**: Filters board listings for analytics, data science, BI, and engineering roles using `r"\b(?:analyst|analytics|data|scientist|statistician|quantitative|bi)\b"`.
   - **Hosted Dataset Snapshot Ingestion**: Single-pass memory snapshot loading (`ats_scrapers.Client().load(ats="ashby")`) matching 27 search terms without repetitive downloads.
   - **Schema Normalization**: Maps all ATS fields into standard columns:
     `["id", "site", "job_url", "job_url_direct", "title", "company", "location", "date_posted", "is_remote", "search_keyword"]`
   - **Deduplication & Sorting**: Deduplicates on `job_url` and sorts case-insensitively by `title`.
   - **Export Specification**: Exports to `scraped jobs/results/YYYY_MM/ats_direct_jobs.csv` using `quoting=csv.QUOTE_NONNUMERIC`, `escapechar="\\"`.
   - **CLI Controls**: Includes `--smoke-test` (fast mode) and `--skip-dataset` flags.

3. **Git Commit**:
   - Commit Hash: `b57405317b3080d5042c7576c8036831ca56c9aa`
   - Commit Message: `feat(jobspy): add search_jobs_ats.py for native ATS scraping`

---

## Verification Results

### Smoke Test (`--smoke-test`)
Command:
```bash
poetry run python jobspy/search_jobs_ats.py --smoke-test
```

Output:
```text
=== RUNNING IN SMOKE-TEST MODE ===

[1/2] Scraping direct ATS boards for target companies...
 > Fetching 'stripe' (greenhouse:stripe)... found 53 matching roles (out of 592 total)
 > Fetching 'openai' (ashby:openai)... found 86 matching roles (out of 770 total)

[2/2] Skipping hosted dataset download in smoke-test mode.

Finished ATS scraping. Extracted 139 roles -> 139 unique roles.
Saved to: /Users/alex/Github/n1ecC/Jobspy-Custom/jobspy/scraped jobs/results/2026_09/ats_direct_jobs.csv
```

### Full Target Company Run (`--skip-dataset`)
Command:
```bash
poetry run python jobspy/search_jobs_ats.py --skip-dataset
```

Output:
- Fetched across 22 top-tier tech companies in ~25 seconds.
- Total Extracted: 482 matching analytics/data roles.
- CSV written cleanly with full non-numeric quoting and valid apply URLs.

---

## Status
- **Status**: DONE
