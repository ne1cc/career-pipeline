# Design Spec: Fresher Recruitment Pipeline & Scraper Expansion

**Date:** 2026-09-11  
**Status:** Approved by User  
**Target Profile:** Early-career candidate / fresher targeting Data & Analytics (Primary), Product / Business Operations (Secondary), and Early-Career Software Engineering (Tertiary).  
**Work Authorization & Location:** US Citizen / Permanent Resident (Unrestricted work authorization, no visa sponsorship needed); Seattle / West Coast + Nationwide Remote.

---

## 1. Overview & Goals

The objective of this enhancement is to transform the JobSpy custom pipeline into a specialized, high-yield recruitment engine for early-career roles while maintaining complete visibility into raw scrapes:
1. **Maximize Discovery Volume**: Activate dormant MSBA scrapers and expand search terms heavily across Data & Analytics (including Analytics Engineer and Full Stack Analytics Engineer), Product / BizOps, and Junior SWE.
2. **Preserve Complete Raw Data**: Keep 100% of scraped listings across all regions and sources in individual Excel tabs for exploratory browsing.
3. **High-Signal "Fresher Spotlight" Action Tab**: Front-load a noise-free `Action Items` tab that filters out senior/management roles, deduplicates cross-platform postings, and ranks listings by freshness, application speed, and entry-level accessibility.

---

## 2. Scraper Architecture & Script Coordination

[`run_parallel.py`](file:///Users/alexandersun/Github/n1ecC/Jobspy-Custom/run_parallel.py) and [`run_pipeline.py`](file:///Users/alexandersun/Github/n1ecC/Jobspy-Custom/run_pipeline.py) will coordinate **8 parallel scraper jobs**:

| Script | Geographic / Source Focus | Excel Output Tab |
|---|---|---|
| `jobspy/search_jobs_remote.py` | US Nationwide Remote roles | `Remote Jobs` |
| `jobspy/search_jobs_seattle.py` | Seattle Metro area | `Seattle Jobs` |
| `jobspy/search_jobs_seattle_entry_level.py` | Seattle entry-level filters | `Seattle Entry Level` |
| `jobspy/search_jobs_west.py` | Seattle, SF, Los Angeles metro areas | `West Coast Jobs` |
| `jobspy/search_jobs_seattle_msba.py` | Deep Seattle analytics queries *(newly wired)* | `Seattle MSBA Jobs` |
| `jobspy/search_jobs_msba_west.py` | Deep West Coast analytics queries *(newly wired)* | `West Coast MSBA Jobs` |
| `jobspy/search_jobs_ats.py` | Direct ATS (Greenhouse, Lever, Ashby, Workday) | `ATS Direct Jobs` |
| `jobspy/search_jobs_portals.py` | Tech portals (BuiltIn, Himalayas, Jobicy, RemoteOK) | `Tech Portals Jobs` |

---

## 3. Query Expansion

### Track A: Data & Analytics (Primary Focus)
Expanded to cover specialized and technical analytics roles:
- `Data Analyst`, `Business Analyst`, `Business Intelligence Analyst`, `BI Developer`, `BI Engineer`
- `Analytics Engineer`, `Full Stack Analytics Engineer`
- `Data Scientist`, `Quantitative Analyst`, `Statistician`
- `Product Analyst`, `Marketing Data Analyst`, `Financial Analyst`, `Operations Analyst`
- `Commercial Analytics Analyst`, `Revenue Operations Analyst`, `RevOps Analyst`
- `Insights Analyst`, `Decision Support Analyst`, `Reporting Analyst`, `Dashboard Developer`
- `Customer Analytics Analyst`, `Digital Analytics Analyst`, `Web Analytics Analyst`
- `Data Governance Analyst`, `Data Operations Analyst`

### Track C: Product & Business Operations (Secondary Focus)
- `Associate Product Manager`, `APM`, `Junior Product Manager`
- `Business Operations Associate`, `BizOps Analyst`, `Operations Analyst`
- `Project Coordinator`, `Program Coordinator`
- `Strategy & Operations Associate`, `Strategy Analyst`

### Track B: Junior Software Engineering (Tertiary Focus)
Prefixed specifically to target early-career pipelines without pulling senior bloat:
- `Associate Software Engineer`, `Junior Software Engineer`
- `Software Engineer - Early Career`, `Software Engineer - New Grad`
- `University Graduate Software Engineer`

### Direct ATS & Tech Portal Queries
Add early-career tags (`"New Grad"`, `"Junior"`, `"Associate"`, `"Early Career"`, `"University"`) to [`jobspy/search_jobs_ats.py`](file:///Users/alexandersun/Github/n1ecC/Jobspy-Custom/jobspy/search_jobs_ats.py) and [`jobspy/search_jobs_portals.py`](file:///Users/alexandersun/Github/n1ecC/Jobspy-Custom/jobspy/search_jobs_portals.py).

---

## 4. Fresher Filtering & Priority Ranking Engine

The front-facing **`Action Items (Fresher Spotlight)`** tab in [`pipeline_enrichment.py`](file:///Users/alexandersun/Github/n1ecC/Jobspy-Custom/pipeline_enrichment.py) implements a two-stage filter followed by multi-factor ranking.

### Stage 1: Positive Track Match
Listing `title` must match regex covering Track A, Track C, or Track B titles.

### Stage 2: Negative Senior & High-YOE Exclusion
Prune roles that are disguised senior positions:
- **Title Exclusions:**  
  `\b(?:senior|sr\b|sr\.|lead|principal|staff|manager|director|vp|vice president|head of|chief|architect|distinguished|fellow|iii|iv|v)\b`
- **Description Exclusions:**  
  Exclude postings whose descriptions explicitly demand `5+`, `6+`, `7+`, `8+`, `10+` years of experience (`r'\b(?:[5-9]|1[0-9])\+\s*(?:years|yrs)\b'`). Roles specifying `0-2`, `1-3`, `0-3`, `entry level`, or asking for degree qualifications without excessive YOE are preserved.

### Stage 3: Priority Scoring Algorithm
Each surviving role is scored to determine display order:
```
Priority Score = (is_new × 5)
               + (is_fresh_post × 3)
               + (explicit_entry_level × 3)
               + (direct_ats_or_easy_apply × 2)
               + (track_weight)
```
- `is_new`: `5` points if discovered on this daily run (not present in `.pipeline_state/previous_job_urls.json`).
- `is_fresh_post`: `3` points if posted within the last 2 days.
- `explicit_entry_level`: `3` points if the title contains `new grad`, `junior`, `associate`, `entry level`, `early career`, or `university`.
- `direct_ats_or_easy_apply`: `2` points for Greenhouse, Lever, Ashby, Workday, or LinkedIn Easy Apply.
- `track_weight`: `+2` for Data & Analytics (Track A), `+1` for Product/BizOps (Track C), `+1` for SWE (Track B).

Roles are sorted in descending order of `Priority Score`, then by `date_posted` descending.

### Stage 4: Cross-Platform Deduplication
If the same job appears on both a general board (Indeed/LinkedIn) and an ATS Direct source, deduplicate on `(company_normalized, title_normalized)`, retaining the ATS Direct listing with the highest priority apply type.

---

## 5. Workbook & Email Delivery Format

1. **Sheet Layout**:
   - **Tab 1**: `Action Items` — Filtered, deduplicated, priority-scored fresher jobs with color-highlighting:
     - Blue row: Both New and Fresh (≤ 48 hours)
     - Green row: New listing discovered today
     - Yellow row: Fresh post (≤ 48 hours)
   - **Tabs 2 to 9**: Unfiltered raw scrapes for each of the 8 scrapers, complete with clickable URLs and autosized columns.
2. **Email Body**:
   - Total jobs scraped across all 8 segments.
   - Count of new jobs, fresh posts, and total curated Action Items.
   - Instructions pointing directly to the Action Items tab.

---

## 6. Error Handling & Testing

- **Per-Scraper Isolation**: In `run_parallel.py`, individual scraper failures do not abort the pipeline.
- **Graceful Fallbacks**: If description parsing fails or is empty, fallback to title-based heuristic.
- **Verification Plan**:
  1. Unit test the positive matcher, negative senior filter, and priority scoring on sample synthetic job datasets.
  2. Perform a dry-run synthesis of `build_excel_workbook` using existing results to ensure all 8 tabs compile without errors.
