# ATS Scrapers Integration Design Specification

**Author**: Antigravity & Alex Sun  
**Date**: 2026-09-02  
**Target Repository**: `Jobspy-Custom` (`/Users/alex/Github/n1ecC/Jobspy-Custom`)  
**Status**: Approved for Implementation Plan  

---

## 1. Overview & Motivation

`JobSpy` aggregates job postings from consumer job search engines (LinkedIn, Indeed, Glassdoor, ZipRecruiter, and Google Jobs). While this provides extensive breadth, aggregate boards frequently suffer from:
1. Anti-bot defenses, IP throttling, and CAPTCHAs.
2. Ghost, expired, or duplicate job postings.
3. Obfuscated or redirected application URLs.

By contrast, Applicant Tracking Systems (ATS) like **Greenhouse**, **Lever**, **Ashby**, **Workday**, and **SmartRecruiters** are the authoritative primary sources of truth for employer job openings. Integrating native ATS scraping via [`kalil0321/ats-scrapers`](https://github.com/kalil0321/ats-scrapers) and direct JSON APIs provides:
* 100% verified, active job postings with zero ghost listings.
* Direct candidate application links (no redirect chains).
* Unobstructed access via backend endpoints, bypassing browser DOM automation.
* Rich metadata (exact department, location, compensation ranges, date posted).

---

## 2. Requirements & Scope

### Functional Requirements
1. **Keyword Parity**: Search the identical 27+ Data, Analytics, Business Intelligence, and Consulting job titles configured across `JobSpy` scripts.
2. **Dual ATS Ingestion Modes**:
   - **Hosted ATS Index Search**: Query `ats_scrapers.search()` across the hosted dataset for each search term with remote and US location filtering.
   - **Direct Target Company ATS Scraper**: Query curated target employers (AI labs, cloud platforms, top tech companies, West Coast / Seattle employers) directly across Greenhouse, Lever, Ashby, and Workday.
3. **Normalized JobSpy Schema**: Transform ATS job records into the standard JobSpy schema:
   - `id`, `title`, `company`, `location`, `job_url`, `job_url_direct`, `date_posted`, `is_remote`, `site`, `search_keyword`.
4. **Pipeline Orchestration**:
   - Integrate `search_jobs_ats.py` into `run_parallel.py` to run concurrently with existing scraper scripts.
   - Save output to `jobspy/scraped jobs/results/YYYY_MM/ats_direct_jobs.csv`.
5. **Reporting & Enrichment**:
   - Integrate `ats_direct_jobs.csv` into `run_pipeline.py`.
   - Create **"ATS Direct Jobs"** and **"ATS Direct Jobs (Filtered)"** tabs in the daily Excel report.
   - Enhance `pipeline_enrichment.py` to identify ATS apply types (`Greenhouse Direct`, `Lever Direct`, `Ashby Direct`, `Workday Direct`).
   - Include new and fresh ATS listings in the **"Action Items"** priority sheet and the daily email report.

### Non-Functional Requirements
- Graceful degradation: If `ats-scrapers` encounters network or query timeouts, log the error without breaking other parallel tasks.
- Zero regression on existing scripts (`search_jobs_remote.py`, `search_jobs_seattle.py`, etc.).

---

## 3. Architecture & Data Flow

```
+-----------------------------------------------------------------------------------+
|                               run_parallel.py                                     |
|                                                                                   |
|  +---------------------------+   +-----------------------+   +-----------------+  |
|  |   search_jobs_remote.py   |   | search_jobs_seattle.py|   |search_jobs_ats  |  |
|  |     (JobSpy Aggregator)   |   |  (JobSpy Aggregator)  |   | (ATS Scrapers)  |  |
|  +-------------+-------------+   +-----------+-----------+   +--------+--------+  |
+----------------|-----------------------------|------------------------|-----------+
                 v                             v                        v
    all_city_remote_jobs.csv            seattle_jobs.csv        ats_direct_jobs.csv
                 |                             |                        |
                 +-----------------------------+------------------------+
                                               v
+-----------------------------------------------------------------------------------+
|                                run_pipeline.py                                    |
|                                                                                   |
|  1. Ingest all CSVs from results directory                                        |
|  2. Apply pipeline_enrichment.py (classify apply type, tag is_new / is_fresh_post) |
|  3. Build Excel Workbook:                                                         |
|     - Action Items (Consolidated high-priority roles)                             |
|     - ATS Direct Jobs & ATS Direct Jobs (Filtered)                                |
|     - Remote Jobs, Seattle Jobs, West Coast Jobs, etc.                            |
|  4. Dispatch formatted email with Excel attachment                               |
+-----------------------------------------------------------------------------------+
```

---

## 4. Detailed Component Design

### 4.1 Dependency Setup
Add `ats-scrapers` to `pyproject.toml` / Poetry virtual environment:
```bash
poetry run pip install "ats-scrapers[scrapers]"
```

### 4.2 ATS Scraper Script (`jobspy/search_jobs_ats.py`)
* **Search Terms**:
  ```python
  SEARCH_TERMS = [
      "Data Analyst", "Business Analyst", "Data Scientist", "Statistician",
      "Quantitative Analyst", "Operations Research Analyst", "Product Analyst",
      "Growth Analyst", "Analytics Engineer", "Business Intelligence Analyst",
      "BI Developer", "BI Engineer", "Financial Analyst", "Market Research Analyst",
      "Marketing Data Analyst", "Marketing Analytics Specialist", "Supply Chain Analyst",
      "Pricing Analyst", "Revenue Analyst", "Sales Operations Analyst", "Risk Analyst",
      "Business Analytics Consultant", "Computer Systems Analyst", "Programmer Analyst",
      "Associate Data & Analytics", "Technology Advisory Associate", "Strategy Consultant"
  ]
  ```
* **Target Company Registry**:
  Curated list of prominent tech, AI, finance, and analytics-driven employers utilizing supported ATS platforms:
  - **Greenhouse**: Stripe, Airbnb, DoorDash, Figma, Pinterest, Instacart, Databricks, Snowflake, Robinhood, Dropbox, Reddit.
  - **Lever**: Spotify, Eventbrite.
  - **Ashby**: OpenAI, Anthropic, Ramp, Notion, Perplexity, Retool, Cursor, Linear, Vercel.
  - **Workday / SmartRecruiters**: Salesforce, Visa, Adobe.
* **Extraction Logic**:
  1. Iterate across search terms via `ats_scrapers.search(query=term, remote=True)` (and `remote=False` for US).
  2. Iterate across target company list via `get_scraper_for_url()` or scraper classes (`GreenhouseScraper`, `LeverScraper`, `AshbyScraper`).
  3. Filter target company boards with regex `\b(?:analyst|analytics|data|scientist|bi|quantitative)\b`.
  4. Standardize columns to match `JobSpy`:
     - `site` = `"ats"`
     - `job_url` = candidate apply URL
     - `job_url_direct` = direct URL
     - `title`, `company`, `location`, `date_posted`, `is_remote`
  5. Deduplicate by `job_url` and sort alphabetically by `title`.
  6. Output to `scraped jobs/results/YYYY_MM/ats_direct_jobs.csv`.

### 4.3 Parallel Runner (`run_parallel.py`)
* Update `scripts` list to include `jobspy_dir / "search_jobs_ats.py"`.
* Update `expected_outputs` dictionary:
  ```python
  expected_outputs = {
      "search_jobs_remote.py": "all_city_remote_jobs.csv",
      "search_jobs_seattle.py": "seattle_jobs.csv",
      "search_jobs_seattle_entry_level.py": "seattle_entry_level_jobs.csv",
      "search_jobs_west.py": "west_coast_jobs.csv",
      "search_jobs_ats.py": "ats_direct_jobs.csv",
  }
  ```

### 4.4 Enrichment Engine (`pipeline_enrichment.py`)
* Update `classify_apply_type`:
  - Detect Greenhouse domains (`boards.greenhouse.io`, `greenhouse.io`) -> `"Greenhouse Direct"`
  - Detect Lever domains (`jobs.lever.co`, `lever.co`) -> `"Lever Direct"`
  - Detect Ashby domains (`jobs.ashbyhq.com`, `ashbyhq.com`) -> `"Ashby Direct"`
  - Detect Workday domains (`myworkdayjobs.com`) -> `"Workday Direct"`
  - Detect SmartRecruiters domains (`smartrecruiters.com`) -> `"SmartRecruiters Direct"`
  - Fallback for `site == "ats"` -> `"Company ATS Direct"`

### 4.5 Pipeline Master (`run_pipeline.py`)
* Register `"ats_direct_jobs.csv": "ATS Direct Jobs"` in `expected_outputs`.
* Produce both standard and filtered tabs (`"ATS Direct Jobs"` and `"ATS Direct Jobs (Filtered)"`).
* Include in `Action Items` priority ranking and email metrics summary.

---

## 5. Testing & Verification

1. **Dependency Verification**: Verify `ats_scrapers` can be imported in the Poetry virtualenv without error.
2. **Unit / Smoke Test**: Run `python jobspy/search_jobs_ats.py` standalone on a subset of terms and verify that `ats_direct_jobs.csv` is populated with valid rows and correct column schema.
3. **Parallel Runner Test**: Verify `python run_parallel.py` spawns `search_jobs_ats.py` and monitors it to successful completion.
4. **Pipeline Compilation Test**: Verify `python run_pipeline.py` creates the Excel file with the `"ATS Direct Jobs"` tabs and correctly applies styling, hyperlinks, and action item highlights.
