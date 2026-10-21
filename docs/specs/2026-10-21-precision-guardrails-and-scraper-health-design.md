# Precision Guardrails & Scraper Health Design Specification

**Author**: Antigravity & Alex Sun  
**Date**: 2026-10-21  
**Target Repository**: `career-pipeline` (`/Users/alex/Github/ne1cc/career-pipeline`)  
**Status**: Pending User Spec Review  

---

## 1. Overview & Motivation

An audit of the live DuckDB database ([`data/job_funnel.duckdb`](file:///Users/alex/Github/ne1cc/career-pipeline/data/job_funnel.duckdb), `47,090` total rows; `11,567` `QUEUED` rows above `min_score = 10.0`) and the parallel scraping orchestrator ([`run_parallel.py`](file:///Users/alex/Github/ne1cc/career-pipeline/run_parallel.py), [`run_pipeline.py`](file:///Users/alex/Github/ne1cc/career-pipeline/run_pipeline.py)) identified two high-leverage bottlenecks:

### 1.1 Upstream Signal-to-Noise Bottleneck (Sub-Project A)
1. **Unconditional Boost Leak (`6,462` queued jobs)**:
   - In [`cockpit/normalizer.py`](file:///Users/alex/Github/ne1cc/career-pipeline/cockpit/normalizer.py#L78-L103), early-career title boosts (`+15.0` for `"associate"`, `"intern"`, `"i"`, `"1"`) and location boosts (`+10.0` for `"seattle"`, `"wa"`, `"remote"`) are applied unconditionally—even when a posting has zero technical keyword matches (`total == 0.0`) and does not match any target role archetype.
   - *Observed impact*: Non-technical roles such as `"Warehouse Associate"` (`28.0`), `"Workforce Specialist I"` (`30.0`), and `"Material Handler I/II"` (`20.0`) rank near the top of the queue.
2. **Seniority Boundary & Taxonomy Gaps (`582` queued jobs)**:
   - [`config/scoring_rules.toml`](file:///Users/alex/Github/ne1cc/career-pipeline/config/scoring_rules.toml#L10-L12) only listed `"sr."` (with a trailing period) instead of `"sr"`. Because `_has()` checks `(?<![a-z0-9])...(?![a-z0-9])` after `re.escape()`, `"Sr AI Engineer"` and `"Sr Enterprise Risk Analyst"` bypassed the penalty completely.
   - Roman numeral seniority levels (`III`, `IV`, `V`), spelled-out executive titles (`"Vice President"`), and technical leadership titles (`"Architect"`, `"Distinguished"`, `"Fellow"`) were not penalized, or a `-45.0` penalty was insufficient when offset by `+15.0` boost + `+10.0` location + keyword weights.
3. **Unchecked JD Experience & Clearance Requirements (`1,330` queued jobs)**:
   - Disqualifiers such as `4+` years of experience, active security clearances (`TS/SCI`, `Top Secret`, `Polygraph`), and `PhD required` appear inside the job description (`description`) rather than the title.

### 1.2 Scraper Reliability & Silent Degradation Bottleneck (Sub-Project B)
1. **Thundering Herd Launch**: [`run_parallel.py`](file:///Users/alex/Github/ne1cc/career-pipeline/run_parallel.py#L135-L180) launches all 8 scrapers simultaneously at `t = 0s`, causing 6 JobSpy scripts to hit LinkedIn, Indeed, Glassdoor, and ZipRecruiter from the same IP at the exact same instant.
2. **No Auto-Retry on Transient Failures or Empty Outputs**: If a scraper exits non-zero or writes `0` rows due to a transient `429` or TLS timeout, no retry is attempted.
3. **Lack of Yield Anomaly Detection & Alerting**: If an upstream board changes its DOM or blocks requests (returning `0` rows while exiting `0`), the pipeline finishes silently without alerting the user in macOS or surfacing yield drop-offs in `cockpit pipeline`.

---

## 2. Sub-Project A: Multi-Layer Precision Scoring & Negative Guardrails

### 2.1 Design Goals
- Apply a heavy configurable penalty (`-100.0` points) to disqualified roles so they drop well below `min_score = 10.0` (hiding them from `QUEUED` and `72H` views and scoreboard counts) while preserving every record in DuckDB for explicit search/auditing.
- Require relevance before awarding early-career or location boosts.
- Accurately parse Years of Experience (`YOE >= 4`) from JD text with false-positive immunity against company history, age requirements, and degree-substitution clauses.
- Keep [`pipeline_enrichment.py`](file:///Users/alex/Github/ne1cc/career-pipeline/pipeline_enrichment.py) and [`cockpit/normalizer.py`](file:///Users/alex/Github/ne1cc/career-pipeline/cockpit/normalizer.py) aligned on seniority and YOE filtering.

### 2.2 Configuration Schema ([`config/scoring_rules.toml`](file:///Users/alex/Github/ne1cc/career-pipeline/config/scoring_rules.toml))

```toml
[display]
min_score = 10.0

[penalties]
points = 100.0
title_contains = [
  "staff", "principal", "director", "vp", "vice president",
  "senior", "sr", "sr.", "lead", "head of", "chief",
  "fellow", "distinguished", "supervisor", "supervisory",
  "iii", "iv", "v", "level 3", "level 4", "level 5",
  "l5", "l6", "l7", "ic4", "ic5", "ic6",
  "phd intern", "phd residency", "(phd)", "postdoc", "postdoctoral"
]
# Contextual title disqualifiers: penalized unless exempted by an early-career/product modifier
architect_exempt = ["associate", "junior", "intern", "new grad", "entry"]
manager_exempt = ["product manager", "program manager", "project manager", "account manager", "client manager"]

[disqualifiers]
penalty = 100.0
max_yoe = 4

# Non-target titles that should never be boosted into the queue
title_exclude = [
  "sales development", "business development", "sdr", "bdr",
  "account executive", "sales representative", "store associate",
  "retail associate", "warehouse associate", "warehouse", "delivery associate",
  "material handler", "forklift", "driver", "janitor", "custodian",
  "mechanic", "electrician", "hvac", "plumber", "carpenter",
  "nurse", "rn", "lpn", "physician", "therapist", "pharmacist",
  "dental", "veterinary", "security guard", "security officer",
  "cashier", "barista", "cook", "chef", "recruiter", "talent acquisition",
  "paralegal", "attorney", "counsel", "caregiver", "social worker"
]

# JD body phrases that trigger the -100.0 penalty
description_contains = [
  "ts/sci", "top secret", "polygraph", "ci poly", "full scope poly",
  "active secret clearance", "active dod secret", "security clearance required",
  "must hold an active", "q clearance", "l clearance",
  "phd required", "ph.d. required", "doctorate required", "must have a phd",
  "m.d. required", "j.d. required", "active rn license",
  "unpaid internship", "commission only", "100% commission",
  "corp-to-corp only", "c2c only"
]
```

And expand `[title_archetype]` in [`config/scoring_rules.toml`](file:///Users/alex/Github/ne1cc/career-pipeline/config/scoring_rules.toml#L19-L23):
- **`data_eng`**: `"data engineer"`, `"analytics engineer"`, `"etl"`, `"data platform"`, `"data infrastructure"`, `"big data"`, `"lakehouse"`, `"dataops"`, `"mlops"`, `"database engineer"`, `"data pipeline"`, `"ai engineer"`, `"ml engineer"`, `"machine learning engineer"`, `"data integration"`, `"forward deployed"`
- **`analytics_msba`**: `"analyst"`, `"analytics"`, `"data scientist"`, `"business intelligence"`, `"bi engineer"`, `"bi developer"`, `"decision science"`, `"business analyst"`, `"quantitative"`, `"quant"`, `"product analyst"`, `"operations analyst"`, `"bizops"`, `"business operations"`, `"financial analyst"`, `"marketing analyst"`, `"pricing analyst"`, `"risk analyst"`, `"research analyst"`, `"economist"`, `"statistician"`, `"insights"`
- **`swe_backend`**: `"software"`, `"sde"`, `"swe"`, `"backend"`, `"back end"`, `"full stack"`, `"fullstack"`, `"developer"`, `"devops"`, `"platform engineer"`, `"site reliability"`, `"sre"`, `"systems engineer"`, `"cloud engineer"`, `"security engineer"`, `"cybersecurity"`, `"appsec"`, `"solutions engineer"`, `"integration engineer"`, `"automation engineer"`, `"qa engineer"`, `"test engineer"`, `"application engineer"`

### 2.3 Scoring & Disqualification Algorithm ([`cockpit/normalizer.py`](file:///Users/alex/Github/ne1cc/career-pipeline/cockpit/normalizer.py))

```
score_job(title, description, rules, location, is_remote)
 │
 ├─ 1. Boundary Matcher Fix (_has):
 │     Strip leading/trailing punctuation when checking token boundaries, or match
 │     alphanumeric word boundaries on the core token so "sr." and "(phd)" match reliably.
 │
 ├─ 2. Keyword & Archetype Resolution:
 │     Compute keyword weight totals across data_eng, swe_backend, analytics_msba.
 │     Resolve `from_title` using expanded `title_archetype`.
 │     `is_relevant = (from_title is not None) or (base_keyword_total > 0)`
 │
 ├─ 3. Relevance Gate for Boosts:
 │     If `is_relevant` is True:
 │       - Add +15.0 if title matches `boosts.title_contains`
 │       - Add +10.0 / +5.0 location points
 │     If `is_relevant` is False:
 │       - Skip early-career and location boosts (score remains 0.0).
 │
 ├─ 4. Title Disqualification Check:
 │     Deduct `penalties.points` (100.0) if ANY of:
 │       a) Title matches `penalties.title_contains`
 │       b) Title matches `disqualifiers.title_exclude`
 │       c) Title contains `"architect"` AND NOT any `architect_exempt` term
 │       d) Title contains `"manager"` AND NOT any `manager_exempt` term
 │
 └─ 5. Description Disqualification & YOE Check:
       Deduct `disqualifiers.penalty` (100.0) if ANY of:
         a) Description matches any phrase in `disqualifiers.description_contains`
         b) `extract_required_yoe(description)` returns `yoe >= disqualifiers.max_yoe` (4)
```

### 2.4 Context-Aware YOE Extractor (`extract_required_yoe`)

`extract_required_yoe(description: str) -> int | None` inspects the lowercased job description and extracts candidate experience requirements while filtering out non-qualification year counts:

1. **Candidate YOE Patterns**:
   - **Range pattern**: `\b(\d{1,2})\s*(?:to|-|–)\s*\d{1,2}\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:[\w,/&-]+\s+){0,4}(?:experience|exp\b|background|track record)` → extracts lower bound.
   - **Plus/Direct pattern**: `\b(\d{1,2})\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:[\w,/&-]+\s+){0,4}(?:experience|exp\b|background|track record)` → extracts integer.
   - **Prefix requirement pattern**: `(?:minimum|min\.?|at least|requires?|requiring)\s+(?:of\s+)?(\d{1,2})\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:[\w,/&-]+\s+){0,4}(?:experience|exp\b)?` → extracts integer.
   - **Label colon pattern**: `(?:experience|work experience|professional experience)\s*:\s*(\d{1,2})\+?\s*(?:years?|yrs?)` → extracts integer.
   - **Written number normalization**: Pre-normalizes written numerals `four` → `4`, `five` → `5`, `six` → `6`, `seven` → `7`, `eight` → `8`, `nine` → `9`, `ten` → `10` (including parenthetical forms like `"five (5) years"` → `"5 years"`) before matching.
2. **False-Positive Immunity Filters**:
   - **Company history exclusion**: Any match preceded within 35 characters by `"for over"`, `"founded"`, `"celebrating"`, `"established"`, `"in business"`, `"industry leader"`, `"history of"`, `"serving"`, `"trusted for"`, `"more than \d+ years ago"` is ignored.
   - **Age / Legal / License exclusion**: Any match followed or preceded by `"of age"`, `"years old"`, `"driver's license"`, `"driving"`, `"retain"`, `"warranty"`, `"contract term"` is ignored.
   - **Upper sanity bound**: Any extracted number `> 15` is ignored as company/tenure boilerplate.
3. **Multi-Tier Degree Substitution Rule**:
   - Many early-career JDs state: *"4+ years of experience with a Bachelor's degree, or 2+ years with a Master's degree"* (or *"0-2 years with MS"*).
   - Therefore, if a JD contains multiple valid candidate YOE matches, `extract_required_yoe()` returns the **minimum** positive candidate YOE cited in the qualification block (or if all cited requirements are `>= 4`, returns that value). This ensures roles offering a `0-3` year track for Master's/Bachelor's candidates are **preserved**, while roles whose lowest tier is `4+` years are **penalized**.

---

## 3. Sub-Project B: Scraper Health, Yield Anomaly Detection & Self-Healing

### 3.1 Architecture & Execution Flow

```
run_pipeline.py
 │
 ├─► run_parallel.py
 │     ├─ Wave 1: Launch ATS + Portals immediately; stagger 6 JobSpy scrapers by 2.5s
 │     ├─ Poll & update `.pipeline_state/pipeline_status.json` (with CSV row counts)
 │     └─ Wave 2 (Self-Healing Retry):
 │          Any scraper with exit_code != 0 OR csv_rows < 5 waits 15s cooldown,
 │          then runs 1 automatic retry attempt and logs `[RETRY]` status.
 │
 ├─► cockpit/pipeline_monitor.py (`audit_pipeline_health`)
 │     ├─ Count rows per CSV and per `site` (linkedin, indeed, glassdoor, ats, portals)
 │     ├─ Compare against `.pipeline_state/yield_history.json` (14-day rolling history)
 │     ├─ Classify health: `HEALTHY`, `WARNING` (<25% of 7d median or dead site), `CRITICAL` (failed/0 rows)
 │     └─ Persist health report into `pipeline_status.json` + append today to `yield_history.json`
 │
 ├─► Alerting & Reporting
 │     ├─ `cockpit pipeline` TUI/CLI dashboard shows `Rows`, `Retries`, and `Health` badges
 │     ├─ Daily email report includes a "Pipeline Health & Yield Audit" section if warnings exist
 │     └─ macOS notification (`osascript`) fires on completion with summary / alert banner
```

### 3.2 Component Specifications

#### 1. Staggered Launch & One-Shot Retry ([`run_parallel.py`](file:///Users/alex/Github/ne1cc/career-pipeline/run_parallel.py))
- **Launch Stagger**:
  - `STAGGER_SECONDS = float(os.environ.get("JOBSPY_STAGGER_SEC", "2.5"))`
  - `search_jobs_ats.py` and `search_jobs_portals.py` launch at `t = 0s` without delay.
  - The 6 JobSpy scripts launch with `STAGGER_SECONDS` pause between spawns to avoid simultaneous burst connections to LinkedIn/Glassdoor/Indeed.
- **CSV Row Counting Helper**:
  - `count_csv_rows(csv_path: Path) -> int`: Uses `pd.read_csv(csv_path, usecols=[0])` (or fast line/record count fallback) to count actual job rows after each process finishes.
- **Automatic Retry Wave**:
  - Minimum healthy row threshold: `MIN_EXPECTED_ROWS = 5`.
  - After Wave 1 completes, collect tasks where `exit_code != 0` or `row_count < MIN_EXPECTED_ROWS`.
  - If any tasks qualify (and `os.environ.get("JOBSPY_DISABLE_RETRY") != "1"`):
    - Wait `RETRY_COOLDOWN_SEC = 15.0` (configurable via `JOBSPY_RETRY_COOLDOWN_SEC`).
    - Re-run those tasks sequentially or with `5.0s` stagger, appending to their log files with a `=== RETRY ATTEMPT 1 ===` header.
    - Record `retried: True`, `retry_exit_code`, and updated `row_count` in `task_states`.

#### 2. Rolling Yield History & Anomaly Detector ([`cockpit/pipeline_monitor.py`](file:///Users/alex/Github/ne1cc/career-pipeline/cockpit/pipeline_monitor.py))
- **State File**: `.pipeline_state/yield_history.json`
  ```json
  {
    "runs": [
      {
        "date": "2026-10-21",
        "timestamp": "2026-10-21T08:14:22",
        "tasks": {
          "search_jobs_seattle.py": 412,
          "search_jobs_remote.py": 680
        },
        "sites": {
          "linkedin": 520,
          "indeed": 610,
          "glassdoor": 180,
          "zip_recruiter": 95,
          "ats": 340,
          "portals": 210
        }
      }
    ]
  }
  ```
- **Retention**: Keep the last 14 daily entries (deduplicating same-day runs by keeping the highest-yield run of that date).
- **Anomaly Evaluation (`audit_pipeline_health(repo_root, results_dir, task_states)`)**:
  - For each task in `EXPECTED_OUTPUTS`:
    - `CRITICAL`: `status == "failed"` after retry OR `row_count == 0`.
    - `WARNING`: Historical 7-day median for this task is `>= 20` rows, and today's `row_count < 0.25 * median_7d`.
    - `HEALTHY`: Otherwise.
  - For each site across all generated CSVs:
    - If historical 7-day median for a site (`linkedin`, `indeed`, `glassdoor`, `ats`) is `>= 20` and today's total across all sheets is `0`, emit a `WARNING`: `"Source '<site>' returned 0 jobs across all segments (possible block/DOM change)"`.

#### 3. Dashboard & Notification Integration
- **[`cockpit/pipeline_monitor.py`](file:///Users/alex/Github/ne1cc/career-pipeline/cockpit/pipeline_monitor.py)**:
  - Add a **Rows** column and **Health** indicator (`✓ OK`, `⚠ LOW YIELD`, `✗ ZERO/FAIL`, `↻ RETRIED`) to the Rich task table in `render_pipeline_dashboard()`.
  - If anomalies exist, display a highlighted `Pipeline Health Alerts` panel at the top of `cockpit pipeline`.
- **[`cockpit/macos.py`](file:///Users/alex/Github/ne1cc/career-pipeline/cockpit/macos.py)**:
  - Add `notify_macos(title: str, message: str) -> None` using `osascript -e 'display notification ...'` (no-op if not on Darwin or if subprocess fails).
  - Called at the end of `run_pipeline.py` with either:
    - Healthy: `"Career Pipeline Complete — <N> jobs scraped, <M> new queued"`
    - Degraded: `"Pipeline Warning — <K> anomalies detected (check cockpit pipeline)"`

---

## 4. Verification & Testing Strategy

1. **Unit Tests for Sub-Project A ([`tests/test_cockpit_pipeline.py`](file:///Users/alex/Github/ne1cc/career-pipeline/tests/test_cockpit_pipeline.py) & [`tests/test_fresher_enrichment.py`](file:///Users/alex/Github/ne1cc/career-pipeline/tests/test_fresher_enrichment.py))**:
   - Verify `"Warehouse Associate"` in `"Seattle, WA"` with no tech keywords scores `0.0` (does not receive `+15` or `+10`).
   - Verify `"Sr AI Engineer"`, `"Software Engineer III"`, `"Solutions Architect"`, `"Associate Vice President"` receive the `-100.0` penalty and score `< 10.0`.
   - Verify `"Associate Solutions Architect"` and `"Product Manager Intern"` are exempted from the Architect/Manager penalty.
   - Verify `extract_required_yoe()` catches `"4+ years"`, `"5-7 yrs"`, `"minimum of four years"`, `"experience: 6 years"`, while ignoring `"founded 20 years ago"`, `"for over 10 years"`, `"18 years of age"`, and multi-tier JDs with `"2+ years with Master's or 4+ years with Bachelor's"`.
   - Verify JD clearance/degree triggers (`"TS/SCI"`, `"PhD required"`, `"polygraph"`) apply `-100.0`.
   - Run `cockpit rescore` against [`data/job_funnel.duckdb`](file:///Users/alex/Github/ne1cc/career-pipeline/data/job_funnel.duckdb) and verify the queued count drops from `11,567` to clean, high-signal early-career target roles.
2. **Unit Tests for Sub-Project B ([`tests/test_pipeline_monitor.py`](file:///Users/alex/Github/ne1cc/career-pipeline/tests/test_pipeline_monitor.py))**:
   - Verify `audit_pipeline_health()` detects `0`-row `CRITICAL` tasks, `<25%` 7-day median `WARNING` tasks, and `0`-row site-wide drops.
   - Verify rolling 14-day `yield_history.json` persistence and deduplication.
   - Verify `render_pipeline_dashboard()` renders row counts, retry badges, and health warnings cleanly.
   - Verify `notify_macos()` invokes `osascript` safely via mock subprocess.
