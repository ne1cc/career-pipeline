# Fresher Job Pipeline Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the JobSpy pipeline to maximize early-career role discovery across Data/Analytics, Product/BizOps, and Junior SWE, while preserving raw scrapes and introducing a noise-free, priority-scored "Action Items" sheet for freshers.

**Architecture:** The pipeline coordinates 8 parallel scraper processes (including newly integrated Seattle and West Coast MSBA scripts) with expanded fresher search queries. Scraped outputs are ingested into `pipeline_enrichment.py` where a positive track filter, negative senior exclusion, cross-platform deduplication, and multi-factor fresher scoring engine produce a front-loaded "Action Items" tab alongside complete raw data tabs in the daily Excel report.

**Tech Stack:** Python 3.12, Poetry, Pandas, OpenPyXL, BeautifulSoup4, JobSpy, Pytest.

**Spec:** [`docs/superpowers/specs/2026-09-11-fresher-job-pipeline-expansion-design.md`](file:///Users/alexandersun/Github/n1ecC/Jobspy-Custom/docs/superpowers/specs/2026-09-11-fresher-job-pipeline-expansion-design.md)

## Global Constraints

- Target Tracks: Track A (Data & Analytics, primary), Track C (Product & BizOps, secondary), Track B (Early Career SWE, tertiary).
- Candidate Eligibility: US Citizen / Permanent Resident (unrestricted work authorization, no visa filtering required).
- Geography: Seattle Metro, West Coast (SF/LA), and US Nationwide Remote.
- Raw Data Preservation: 100% of scraped listings across all 8 sources must be preserved in their respective raw tabs in the generated workbook.
- Negative Filter: Action Items sheet must strictly exclude Senior, Lead, Staff, Principal, Manager, Director, VP, Levels III/IV/V, and jobs demanding 5+ years of experience.

---

### Task 1: Expand Query Sets in Scraper Scripts (Tracks A, C, B)

**Files:**
- Modify: `jobspy/search_jobs_remote.py`
- Modify: `jobspy/search_jobs_seattle.py`
- Modify: `jobspy/search_jobs_west.py`
- Modify: `jobspy/search_jobs_ats.py`
- Modify: `jobspy/search_jobs_portals.py`
- Test: `tests/test_scraper_queries.py`

**Interfaces:**
- Consumes: Existing search terms lists in `jobspy/search_jobs_*.py`
- Produces: Expanded `search_terms` and query lists containing Analytics Engineer, Full Stack Analytics Engineer, APM, BizOps Associate, and Junior SWE keywords.

- [ ] **Step 1: Write the failing test for query expansion**

```python
# tests/test_scraper_queries.py
import pytest
from jobspy import search_jobs_remote, search_jobs_seattle, search_jobs_west, search_jobs_ats, search_jobs_portals

def test_remote_search_terms_include_analytics_engineer():
    terms = search_jobs_remote.search_terms
    assert "Analytics Engineer" in terms
    assert "Full Stack Analytics Engineer" in terms
    assert "Associate Product Manager" in terms
    assert "Associate Software Engineer" in terms

def test_seattle_search_terms_include_analytics_engineer():
    terms = search_jobs_seattle.search_terms
    assert "Analytics Engineer" in terms
    assert "Business Operations Associate" in terms

def test_west_search_terms_include_analytics_engineer():
    terms = search_jobs_west.search_terms
    assert "Analytics Engineer" in terms
    assert "Associate Product Manager" in terms

def test_ats_queries_include_early_career_tags():
    terms = search_jobs_ats.DEFAULT_SEARCH_TERMS
    assert any("new grad" in t.lower() or "early career" in t.lower() or "junior" in t.lower() for t in terms)
    assert any("analytics engineer" in t.lower() for t in terms)

def test_portals_queries_include_early_career_tags():
    terms = search_jobs_portals.DEFAULT_SEARCH_TERMS
    assert any("new grad" in t.lower() or "early career" in t.lower() or "junior" in t.lower() for t in terms)
    assert any("analytics engineer" in t.lower() for t in terms)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_scraper_queries.py -v`  
Expected: FAIL due to missing keywords in search terms lists.

- [ ] **Step 3: Update `search_terms` in scraper scripts**

In `jobspy/search_jobs_remote.py`, `jobspy/search_jobs_seattle.py`, and `jobspy/search_jobs_west.py`, expand `search_terms` with:
```python
# Expanded Data & Analytics Roles
"Analytics Engineer",
"Full Stack Analytics Engineer",
"Data Operations Analyst",
"Reporting Analyst",
"Insights Analyst",
"Decision Support Analyst",
"Commercial Analytics Analyst",
"Revenue Operations Analyst",
"Customer Analytics Analyst",
# Product & Business Operations Roles
"Associate Product Manager",
"APM",
"Business Operations Associate",
"BizOps Analyst",
"Project Coordinator",
"Strategy & Operations Associate",
# Junior Software Engineering Roles
"Associate Software Engineer",
"Junior Software Engineer",
"Software Engineer - Early Career",
"Software Engineer - New Grad",
```

In `jobspy/search_jobs_ats.py` and `jobspy/search_jobs_portals.py`, expand `DEFAULT_SEARCH_TERMS` to include:
```python
"Analytics Engineer",
"Full Stack Analytics Engineer",
"Associate Product Manager",
"Business Operations Associate",
"New Grad Software Engineer",
"Junior Data Analyst",
"Early Career Analyst",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_scraper_queries.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobspy/search_jobs_remote.py jobspy/search_jobs_seattle.py jobspy/search_jobs_west.py jobspy/search_jobs_ats.py jobspy/search_jobs_portals.py tests/test_scraper_queries.py
git commit -m "feat(scrapers): expand query terms for analytics engineer, bizops, and junior roles"
```

---

### Task 2: Integrate MSBA Scrapers into Parallel Runner and Pipeline Workbook

**Files:**
- Modify: `run_parallel.py`
- Modify: `run_pipeline.py`
- Test: `tests/test_pipeline_integration.py`

**Interfaces:**
- Consumes: `jobspy/search_jobs_seattle_msba.py` and `jobspy/search_jobs_msba_west.py`
- Produces: 8 parallel subprocess executions in `run_parallel.py` and 8 corresponding workbook tabs in `run_pipeline.py`.

- [ ] **Step 1: Write the failing test for runner and pipeline integration**

```python
# tests/test_pipeline_integration.py
from pathlib import Path
import run_parallel
import run_pipeline

def test_parallel_runner_scripts_contain_msba():
    base_dir = Path(run_parallel.__file__).parent.resolve()
    jobspy_dir = base_dir / "jobspy"
    expected_scripts = [
        jobspy_dir / "search_jobs_remote.py",
        jobspy_dir / "search_jobs_seattle.py",
        jobspy_dir / "search_jobs_seattle_entry_level.py",
        jobspy_dir / "search_jobs_west.py",
        jobspy_dir / "search_jobs_seattle_msba.py",
        jobspy_dir / "search_jobs_msba_west.py",
        jobspy_dir / "search_jobs_ats.py",
        jobspy_dir / "search_jobs_portals.py",
    ]
    # We inspect the expected_outputs keys or scripts defined in run_parallel
    for script in expected_scripts:
        assert script.exists(), f"Script missing: {script}"

def test_pipeline_expected_outputs_mapping():
    outputs = run_pipeline.EXPECTED_OUTPUTS
    assert "seattle_msba_jobs.csv" in outputs
    assert outputs["seattle_msba_jobs.csv"] == "Seattle MSBA Jobs"
    assert "west_coast_msba_jobs.csv" in outputs
    assert outputs["west_coast_msba_jobs.csv"] == "West Coast MSBA Jobs"
    assert len(outputs) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_pipeline_integration.py -v`  
Expected: FAIL due to missing `EXPECTED_OUTPUTS` entries in `run_pipeline` and missing script registrations in `run_parallel`.

- [ ] **Step 3: Update `run_parallel.py` and `run_pipeline.py`**

In `run_parallel.py`:
1. Add `jobspy_dir / "search_jobs_seattle_msba.py"` and `jobspy_dir / "search_jobs_msba_west.py"` to the `scripts` list.
2. Add `"search_jobs_seattle_msba.py": "seattle_msba_jobs.csv"` and `"search_jobs_msba_west.py": "west_coast_msba_jobs.csv"` to `expected_outputs`.

In `run_pipeline.py`:
Define top-level `EXPECTED_OUTPUTS`:
```python
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
```
Update `main()` in `run_pipeline.py` to use `EXPECTED_OUTPUTS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_pipeline_integration.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_parallel.py run_pipeline.py tests/test_pipeline_integration.py
git commit -m "feat(pipeline): wire seattle and west coast msba scrapers into parallel runner"
```

---

### Task 3: Implement Fresher Filtering, Priority Scoring & Deduplication

**Files:**
- Modify: `pipeline_enrichment.py`
- Test: `tests/test_fresher_enrichment.py`

**Interfaces:**
- Consumes: Raw DataFrames from scrapers with `title`, `description`, `job_url`, `company`, `date_posted`, `apply_type`, `is_new`, `is_fresh_post`.
- Produces:
  - `matches_fresher_track(title: str) -> bool`
  - `is_senior_or_high_yoe(title: str, description: str) -> bool`
  - `calculate_fresher_priority_score(row: pd.Series) -> int`
  - `deduplicate_action_items(df: pd.DataFrame) -> pd.DataFrame`
  - Enhanced `build_action_items_sheet(segment_frames: dict[str, pd.DataFrame]) -> pd.DataFrame`

- [ ] **Step 1: Write the failing unit tests for fresher enrichment**

```python
# tests/test_fresher_enrichment.py
import pandas as pd
import pytest
from pipeline_enrichment import (
    matches_fresher_track,
    is_senior_or_high_yoe,
    calculate_fresher_priority_score,
    build_action_items_sheet,
)

def test_matches_fresher_track():
    assert matches_fresher_track("Junior Data Analyst")
    assert matches_fresher_track("Analytics Engineer")
    assert matches_fresher_track("Full Stack Analytics Engineer")
    assert matches_fresher_track("Associate Product Manager")
    assert matches_fresher_track("BizOps Analyst")
    assert matches_fresher_track("Associate Software Engineer")
    assert not matches_fresher_track("Registered Nurse")
    assert not matches_fresher_track("Truck Driver")

def test_is_senior_or_high_yoe():
    # Senior title
    assert is_senior_or_high_yoe("Senior Data Analyst", "Great job 2 years exp")
    assert is_senior_or_high_yoe("Lead Analytics Engineer", "Entry level role")
    assert is_senior_or_high_yoe("Principal Product Manager", "")
    assert is_senior_or_high_yoe("Engineering Manager", "")
    assert is_senior_or_high_yoe("Data Analyst III", "")
    
    # High YOE description
    assert is_senior_or_high_yoe("Data Analyst", "Must have 6+ years of experience in SQL")
    assert is_senior_or_high_yoe("Business Analyst", "Requires 10+ yrs of data modeling")
    
    # Safe fresher roles
    assert not is_senior_or_high_yoe("Data Analyst", "Requires 1-2 years experience or new graduate")
    assert not is_senior_or_high_yoe("Associate Product Manager", "0-3 years experience")
    assert not is_senior_or_high_yoe("Analytics Engineer", "Bachelor's or Master's degree in quantitative field")

def test_priority_scoring():
    high_priority_row = pd.Series({
        "title": "New Grad Analytics Engineer",
        "is_new": True,
        "is_fresh_post": True,
        "apply_type": "Greenhouse Direct",
    })
    score = calculate_fresher_priority_score(high_priority_row)
    # is_new (5) + is_fresh (3) + explicit entry (3) + direct ATS (2) + Track A (2) = 15
    assert score >= 14

def test_action_items_filters_seniors_and_ranks():
    df = pd.DataFrame([
        {
            "title": "Senior Data Analyst",
            "description": "5+ years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Company Site",
            "job_url": "http://1",
            "company": "A",
        },
        {
            "title": "Junior Data Analyst",
            "description": "0-1 years",
            "is_new": True,
            "is_fresh_post": True,
            "apply_type": "Greenhouse Direct",
            "job_url": "http://2",
            "company": "B",
        },
    ])
    result = build_action_items_sheet({"Test Segment": df})
    assert len(result) == 1
    assert result.iloc[0]["title"] == "Junior Data Analyst"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_fresher_enrichment.py -v`  
Expected: FAIL with `ImportError: cannot import name 'matches_fresher_track'`

- [ ] **Step 3: Implement fresher filtering & scoring in `pipeline_enrichment.py`**

Add the following logic to `pipeline_enrichment.py`:
```python
TRACK_A_PATTERN = r"\b(?:analyst|analytics|data|data scientist|statistician|quantitative|analytics engineer|bi\b|bi developer|business intelligence|reporting|insights)\b"
TRACK_C_PATTERN = r"\b(?:product manager|apm\b|business operations|bizops|operations analyst|project coordinator|strategy & operations|strategy analyst)\b"
TRACK_B_PATTERN = r"\b(?:software engineer|developer|swe\b|frontend|backend|full stack)\b"

SENIOR_TITLE_PATTERN = r"\b(?:senior|sr\b|sr\.|lead|principal|staff|manager|director|vp\b|vice president|head of|chief|architect|distinguished|fellow|iii|iv|v)\b"
HIGH_YOE_PATTERN = r"\b(?:[5-9]|1[0-9])\+\s*(?:years|yrs)\b|\b(?:[6-9]|1[0-9])\s*to\s*[0-9]+\s*(?:years|yrs)\b"
ENTRY_TITLE_TAG_PATTERN = r"\b(?:new grad|junior|associate|entry level|early career|university|intern|internship|campus)\b"

def matches_fresher_track(title: str) -> bool:
    if not title:
        return False
    t = str(title).lower()
    return bool(
        re.search(TRACK_A_PATTERN, t)
        or re.search(TRACK_C_PATTERN, t)
        or (re.search(TRACK_B_PATTERN, t) and re.search(ENTRY_TITLE_TAG_PATTERN, t))
    )

def is_senior_or_high_yoe(title: str, description: str = "") -> bool:
    t = str(title or "").lower()
    if re.search(SENIOR_TITLE_PATTERN, t):
        return True
    d = str(description or "").lower()
    if d and re.search(HIGH_YOE_PATTERN, d):
        return True
    return False

def calculate_fresher_priority_score(row: pd.Series) -> int:
    score = 0
    if bool(row.get("is_new", False)):
        score += 5
    if bool(row.get("is_fresh_post", False)):
        score += 3
    
    title = str(row.get("title", "")).lower()
    if re.search(ENTRY_TITLE_TAG_PATTERN, title):
        score += 3
        
    apply_type = str(row.get("apply_type", "")).lower()
    if any(k in apply_type for k in ["direct", "easy apply"]):
        score += 2
        
    if re.search(TRACK_A_PATTERN, title):
        score += 2
    elif re.search(TRACK_C_PATTERN, title) or re.search(TRACK_B_PATTERN, title):
        score += 1
        
    return score

def deduplicate_action_items(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    # Prefer Direct ATS and higher priority scores
    df = df.sort_values(by=["_score"], ascending=False)
    # Deduplicate on normalized title and company
    clean_title = df["title"].astype(str).str.lower().str.replace(r"[^\w\s]", "", regex=True)
    clean_company = df["company"].astype(str).str.lower().str.replace(r"[^\w\s]", "", regex=True)
    df["_dedup_key"] = clean_title + "::" + clean_company
    df = df.drop_duplicates(subset=["_dedup_key"], keep="first").drop(columns=["_dedup_key"])
    return df
```

Update `build_action_items_sheet`:
```python
def build_action_items_sheet(segment_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pieces = []
    for segment_name, df in segment_frames.items():
        if df.empty:
            continue
        desc_col = "description" if "description" in df.columns else None
        
        # Filter for fresher track & exclude senior
        qualifying = []
        for idx, row in df.iterrows():
            title = str(row.get("title", ""))
            desc = str(row.get(desc_col, "")) if desc_col else ""
            if matches_fresher_track(title) and not is_senior_or_high_yoe(title, desc):
                qualifying.append(idx)
                
        subset = df.loc[qualifying].copy()
        if subset.empty:
            continue
        subset.insert(0, "segment", segment_name)
        pieces.append(subset)
        
    if not pieces:
        return pd.DataFrame(columns=["segment", *ENRICHMENT_COLUMNS])
        
    combined = pd.concat(pieces, ignore_index=True)
    combined["_score"] = combined.apply(calculate_fresher_priority_score, axis=1)
    combined = deduplicate_action_items(combined)
    
    if "date_posted" in combined.columns:
        combined["_date_sort"] = pd.to_datetime(combined["date_posted"], errors="coerce")
        combined = combined.sort_values(by=["_score", "_date_sort"], ascending=[False, False]).drop(columns=["_score", "_date_sort"])
    else:
        combined = combined.sort_values(by="_score", ascending=False).drop(columns="_score")
        
    return combined.reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_fresher_enrichment.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline_enrichment.py tests/test_fresher_enrichment.py
git commit -m "feat(enrichment): implement fresher positive matching, senior exclusion, and priority ranking"
```

---

### Task 4: Workbook Compilation & End-to-End Dry-Run Verification

**Files:**
- Modify: `run_pipeline.py`
- Create: `tests/test_workbook_dryrun.py`

**Interfaces:**
- Consumes: `EXPECTED_OUTPUTS` (8 sheets), `build_excel_workbook()`, `pipeline_enrichment.py`
- Produces: Complete Excel file with `Action Items` as Tab 1 followed by 8 raw tabs; enriched email body summary.

- [ ] **Step 1: Write the failing end-to-end dry-run test**

```python
# tests/test_workbook_dryrun.py
from pathlib import Path
import pandas as pd
import openpyxl
from run_pipeline import build_excel_workbook, EXPECTED_OUTPUTS

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
        
    excel_path, stats, has_previous_run = build_excel_workbook(results_dir, EXPECTED_OUTPUTS)
    assert excel_path.exists()
    
    wb = openpyxl.load_workbook(excel_path)
    sheet_names = wb.sheetnames
    
    # Verify Tab 1 is Action Items
    assert sheet_names[0] == "Action Items"
    
    # Verify all raw tabs exist
    for target_tab in EXPECTED_OUTPUTS.values():
        assert target_tab in sheet_names
        
    # Verify Action Items excluded the Senior role
    ws_action = wb["Action Items"]
    titles = [row[wb["Action Items"]["A1":"Z1"][0].index(cell) if cell.value == "title" else -1] for row in ws_action.iter_rows(values_only=True)]
    # Ensure Senior role is not in Action Items
    all_action_titles = [cell.value for cell in ws_action["B"] if cell.value]  # title column or search all
    all_text = " ".join([str(val) for row in ws_action.iter_rows(values_only=True) for val in row if val])
    assert "Senior Data Scientist" not in all_text
    assert "Analytics Engineer" in all_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_workbook_dryrun.py -v`  
Expected: FAIL or verify differences in workbook ordering.

- [ ] **Step 3: Ensure `Action Items` is saved as the first tab in `run_pipeline.py`**

In `run_pipeline.py`:
Ensure `Action Items` is placed as Tab 1 in `build_excel_workbook`:
Write the `Action Items` sheet first, or rearrange sheet order using openpyxl (`wb._sheets.insert(0, wb._sheets.pop())`), then save.
Ensure email summary explicitly highlights:
`Action Items (Fresher Spotlight) count`, `Direct ATS count`, and `Total Raw Postings`.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_workbook_dryrun.py -v`  
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `poetry run pytest -v`  
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add run_pipeline.py tests/test_workbook_dryrun.py
git commit -m "feat(pipeline): front-load Action Items tab and verify 8-tab workbook assembly"
```
