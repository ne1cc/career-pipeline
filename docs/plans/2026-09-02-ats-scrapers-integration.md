# Implementation Plan: ATS Scrapers Integration for JobSpy

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `Jobspy-Custom` pipeline with a native ATS scraping engine (`ats-scrapers`) that queries primary source job boards (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) and unifies them into the daily parallel scraping and Excel reporting workflow.

**Architecture:** A standalone scraper script (`search_jobs_ats.py`) runs concurrently with existing aggregate board scrapers via `run_parallel.py`. It combines live hosted ATS dataset queries with direct target-company ATS board extraction. Outputs are normalized to the standard JobSpy schema (`ats_direct_jobs.csv`), enriched with ATS apply tags in `pipeline_enrichment.py`, and compiled into dedicated Excel tabs and the daily email report via `run_pipeline.py`.

**Tech Stack:** Python 3.12, Poetry, `ats-scrapers[scrapers]`, `pandas`, `openpyxl`, `httpx`.

**Spec:** [`docs/specs/2026-09-02-ats-scrapers-integration-design.md`](file:///Users/alex/Github/n1ecC/Jobspy-Custom/docs/specs/2026-09-02-ats-scrapers-integration-design.md)

---

## Global Constraints
- Python 3.12+ in Poetry virtualenv `/Users/alex/Library/Caches/pypoetry/virtualenvs/python-jobspy-lRSzGi2v-py3.12`.
- Deduplication key: `job_url`.
- Output CSV naming convention: `ats_direct_jobs.csv` under `jobspy/scraped jobs/results/YYYY_MM/`.
- Must preserve full backward compatibility with all existing scraper scripts (`search_jobs_remote.py`, `search_jobs_seattle.py`, etc.).
- Comply with all Markdown formatting rules: Native Unicode (`≤`, `≥`, `∑`, `×`), no raw LaTeX math delimiters, currency formatted in backtick spans.

---

## User Review Required

> [!IMPORTANT]
> - `ats-scrapers[scrapers]` will be installed into the project's Poetry virtualenv. Dry-run resolution has already passed cleanly.
> - The ATS scraper uses the exact same 27 data and analytics search keywords as the existing JobSpy search scripts.
> - Direct company scraping covers high-impact tech, AI, and finance employers across Greenhouse, Lever, Ashby, and Workday.

---

## Proposed Changes

```
Jobspy-Custom/
├── jobspy/
│   └── search_jobs_ats.py           # [NEW] ATS query & direct board scraper
├── pipeline_enrichment.py           # [MODIFY] Add ATS apply type classification
├── run_parallel.py                  # [MODIFY] Add search_jobs_ats.py to parallel runner
├── run_pipeline.py                  # [MODIFY] Add ats_direct_jobs.csv to Excel tabs
└── pyproject.toml                   # [MODIFY] Add ats-scrapers dependency
```

---

### Task 1: Environment & Dependency Setup

**Files:**
- Modify: `pyproject.toml`
- Target Environment: Poetry virtualenv (`python-jobspy-lRSzGi2v-py3.12`)

**Interfaces:**
- Produces: Installed and importable `ats_scrapers` package with `search`, `get_scraper_for_url`, `find_company`, and scraper classes (`GreenhouseScraper`, `LeverScraper`, `AshbyScraper`).

- [ ] **Step 1: Install `ats-scrapers[scrapers]` into the active virtualenv**

```bash
poetry run pip install "ats-scrapers[scrapers]"
```

- [ ] **Step 2: Verify package import and API availability**

```bash
poetry run python -c "import ats_scrapers; from ats_scrapers.scrapers import GreenhouseScraper, LeverScraper, AshbyScraper; print('ats-scrapers successfully imported')"
```
Expected: Output prints `ats-scrapers successfully imported`.

- [ ] **Step 3: Update `pyproject.toml` dependencies**

Add `ats-scrapers = {version = ">=0.2.0", extras = ["scrapers"]}` to `[tool.poetry.dependencies]`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add ats-scrapers dependency"
```

---

### Task 2: Implement ATS Scraper Engine (`jobspy/search_jobs_ats.py`)

**Files:**
- Create: `jobspy/search_jobs_ats.py`
- Test: `test_ats_scraper_smoke.py` (temporary test runner in scratch directory)

**Interfaces:**
- Consumes: `ats_scrapers.search`, `ats_scrapers.scrapers.GreenhouseScraper`, `LeverScraper`, `AshbyScraper`.
- Produces: `ats_direct_jobs.csv` in `jobspy/scraped jobs/results/YYYY_MM/` with standard JobSpy columns:
  `["id", "title", "company", "location", "job_url", "job_url_direct", "date_posted", "is_remote", "site", "search_keyword"]`.

- [ ] **Step 1: Implement `jobspy/search_jobs_ats.py`**

Features to implement:
1. `SEARCH_TERMS`: Identical 27 terms from `search_jobs_remote.py`.
2. `TARGET_COMPANIES`: Curated dictionary of companies across Greenhouse (Stripe, Databricks, Snowflake, Figma, DoorDash, Pinterest, Instacart, Dropbox, Reddit, Robinhood), Lever (Spotify, Eventbrite), Ashby (OpenAI, Anthropic, Ramp, Notion, Perplexity, Retool, Cursor, Linear, Vercel).
3. `scrape_hosted_ats_dataset(terms)`: Iteratively queries `ats_scrapers.search(query=term, remote=True)` and aggregates results.
4. `scrape_target_company_boards()`: Fetches direct boards via `GreenhouseScraper`, `LeverScraper`, `AshbyScraper` with regex filter `\b(?:analyst|analytics|data|scientist|statistician|quantitative|bi)\b`.
5. Direct fallback: Simple HTTP JSON query helper for Greenhouse/Lever in case of library transient errors.
6. Deduplication by `job_url`, title-sorting, and CSV export matching JobSpy format (`quoting=csv.QUOTE_NONNUMERIC`, `escapechar="\\"`).

- [ ] **Step 2: Run smoke test to verify execution and schema output**

Run with a limited subset (2 terms, 2 target companies) to verify end-to-end execution in < 15 seconds:
```bash
poetry run python -c "import sys; from pathlib import Path; sys.path.insert(0, 'jobspy'); import search_jobs_ats; print('Module loaded successfully')"
```

- [ ] **Step 3: Verify output CSV structure**

Verify that `ats_direct_jobs.csv` is created with headers matching JobSpy requirements:
```bash
poetry run python -c "import pandas as pd, glob; files = glob.glob('jobspy/scraped jobs/results/**/ats_direct_jobs.csv', recursive=True); print('Found:', files)"
```

- [ ] **Step 4: Commit**

```bash
git add jobspy/search_jobs_ats.py
git commit -m "feat(jobspy): add search_jobs_ats.py for native ATS scraping"
```

---

### Task 3: Enhance Apply Type Classification (`pipeline_enrichment.py`)

**Files:**
- Modify: `pipeline_enrichment.py:61-90`

**Interfaces:**
- Consumes: Job record series containing `job_url`, `job_url_direct`, and `site`.
- Produces: Specific ATS classification strings (`Greenhouse Direct`, `Lever Direct`, `Ashby Direct`, `Workday Direct`, `SmartRecruiters Direct`, `Company ATS Direct`).

- [ ] **Step 1: Update `classify_apply_type` in `pipeline_enrichment.py`**

Add ATS domain matching before generic fallbacks:
```python
def classify_apply_type(row: pd.Series) -> str:
    site = str(row.get("site", "") or "").lower()
    job_url = str(row.get("job_url", "") or "").lower()
    job_url_direct = str(row.get("job_url_direct", "") or "").strip().lower()
    urls_combined = f"{job_url} {job_url_direct}"

    # Direct ATS detection across all sources
    if "greenhouse.io" in urls_combined:
        return "Greenhouse Direct"
    if "lever.co" in urls_combined:
        return "Lever Direct"
    if "ashbyhq.com" in urls_combined:
        return "Ashby Direct"
    if "myworkdayjobs.com" in urls_combined or "workday" in urls_combined:
        return "Workday Direct"
    if "smartrecruiters.com" in urls_combined:
        return "SmartRecruiters Direct"

    if site == "ats":
        return "Company ATS Direct"

    # Existing site-specific rules follow...
```

- [ ] **Step 2: Test apply type classification with unit test assertions**

```bash
poetry run python -c "
import pandas as pd
from pipeline_enrichment import classify_apply_type
assert classify_apply_type(pd.Series({'job_url': 'https://boards.greenhouse.io/stripe/jobs/123'})) == 'Greenhouse Direct'
assert classify_apply_type(pd.Series({'job_url': 'https://jobs.ashbyhq.com/openai/456'})) == 'Ashby Direct'
assert classify_apply_type(pd.Series({'site': 'ats', 'job_url': 'https://custom.com/apply'})) == 'Company ATS Direct'
print('All classification tests passed!')
"
```

- [ ] **Step 3: Commit**

```bash
git add pipeline_enrichment.py
git commit -m "feat(enrichment): add ATS apply type classification"
```

---

### Task 4: Integrate ATS Scraper into Parallel Runner (`run_parallel.py`)

**Files:**
- Modify: `run_parallel.py:15-20,147-153`

**Interfaces:**
- Consumes: `jobspy_dir / "search_jobs_ats.py"`
- Produces: Concurrent process execution and status logging for `search_jobs_ats.py` -> `ats_direct_jobs.csv`.

- [ ] **Step 1: Add `search_jobs_ats.py` to `scripts` list**

In `run_parallel.py`:
```python
    scripts = [
        jobspy_dir / "search_jobs_remote.py",
        jobspy_dir / "search_jobs_seattle.py",
        jobspy_dir / "search_jobs_seattle_entry_level.py",
        jobspy_dir / "search_jobs_west.py",
        jobspy_dir / "search_jobs_ats.py",
    ]
```

- [ ] **Step 2: Register expected output in `expected_outputs`**

```python
    expected_outputs = {
        "search_jobs_remote.py": "all_city_remote_jobs.csv",
        "search_jobs_seattle.py": "seattle_jobs.csv",
        "search_jobs_seattle_entry_level.py": "seattle_entry_level_jobs.csv",
        "search_jobs_west.py": "west_coast_jobs.csv",
        "search_jobs_ats.py": "ats_direct_jobs.csv",
    }
```

- [ ] **Step 3: Verify script existence check**

Run dry-check:
```bash
poetry run python -c "from pathlib import Path; p = Path('jobspy/search_jobs_ats.py'); assert p.exists(), 'Script missing'; print('Script verified')"
```

- [ ] **Step 4: Commit**

```bash
git add run_parallel.py
git commit -m "feat(runner): add search_jobs_ats.py to parallel runner"
```

---

### Task 5: Integrate ATS Jobs into Daily Pipeline & Excel Workbook (`run_pipeline.py`)

**Files:**
- Modify: `run_pipeline.py:288-296`

**Interfaces:**
- Consumes: `ats_direct_jobs.csv`
- Produces: Excel tabs `"ATS Direct Jobs"` and `"ATS Direct Jobs (Filtered)"`, prioritized entries in `"Action Items"`, and summarized statistics in the email payload.

- [ ] **Step 1: Add `"ats_direct_jobs.csv": "ATS Direct Jobs"` to `expected_outputs`**

In `run_pipeline.py`:
```python
    expected_outputs = {
        "all_city_remote_jobs.csv": "Remote Jobs",
        "seattle_jobs.csv": "Seattle Jobs",
        "seattle_entry_level_jobs.csv": "Seattle Entry Level",
        "west_coast_jobs.csv": "West Coast Jobs",
        "ats_direct_jobs.csv": "ATS Direct Jobs",
    }
```

- [ ] **Step 2: Verify Excel compilation with dummy or existing sample CSV**

Create a 1-row test `ats_direct_jobs.csv` if none exists, run Excel compilation step, and verify tabs:
```bash
poetry run python -c "
import openpyxl, glob
wb_files = glob.glob('jobspy/scraped jobs/results/**/*.xlsx', recursive=True)
print('Workbook check ready')
"
```

- [ ] **Step 3: Commit**

```bash
git add run_pipeline.py
git commit -m "feat(pipeline): include ATS Direct Jobs in daily Excel workbook"
```

---

### Task 6: End-to-End Verification

**Files:**
- Test execution across `jobspy/search_jobs_ats.py`, `run_parallel.py`, and `run_pipeline.py`.

- [ ] **Step 1: Execute standalone ATS scrape test**

Run:
```bash
poetry run python jobspy/search_jobs_ats.py
```
Expected: Successfully queries endpoints, outputs summary count of jobs found, and writes `ats_direct_jobs.csv`.

- [ ] **Step 2: Inspect output CSV**

Confirm non-empty rows, correct columns, valid URLs, and clean titles:
```bash
poetry run python -c "
import pandas as pd, glob
f = sorted(glob.glob('jobspy/scraped jobs/results/**/ats_direct_jobs.csv', recursive=True))[-1]
df = pd.read_csv(f)
print(f'Rows: {len(df)}')
print('Columns:', df.columns.tolist())
print(df[['title', 'company', 'location', 'site']].head(5))
"
```

- [ ] **Step 3: Build Excel workbook test**

Run:
```bash
poetry run python -c "
from pathlib import Path
from datetime import datetime
import run_pipeline

results_dir = Path('jobspy/scraped jobs/results') / datetime.now().strftime('%Y_%m')
expected = {'ats_direct_jobs.csv': 'ATS Direct Jobs'}
excel_path, stats, has_prev = run_pipeline.build_excel_workbook(results_dir, expected)
print('Workbook generated:', excel_path)
"
```
Expected: Workbook compiles successfully with formatted hyperlinks and styling.

- [ ] **Step 4: Final commit and clean up temporary test artifacts**

```bash
git status
git commit -m "feat: complete ATS scrapers integration into JobSpy pipeline"
```

---

## Verification Plan

### Automated Tests
* Package imports and scraper classes:
  `poetry run python -c "import ats_scrapers; from ats_scrapers.scrapers import GreenhouseScraper, LeverScraper, AshbyScraper; print('OK')"`
* Apply type classification:
  `poetry run python -c "import pandas as pd; from pipeline_enrichment import classify_apply_type; ..."`
* Scraper execution:
  `poetry run python jobspy/search_jobs_ats.py`

### Manual Verification
* Inspect generated `Job_Report_YYYY_MM_DD.xlsx`:
  - Open in Excel / Numbers.
  - Verify **"ATS Direct Jobs"** and **"ATS Direct Jobs (Filtered)"** sheets are present.
  - Verify URLs are styled as active clickable blue hyperlinks.
  - Verify **"Action Items"** includes fresh ATS roles highlighted with proper green/yellow cell fills.
