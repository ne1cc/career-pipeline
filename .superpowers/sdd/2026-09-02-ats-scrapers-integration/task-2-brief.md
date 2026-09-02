# Task 2 Brief: Implement ATS Scraper Engine (`jobspy/search_jobs_ats.py`)

## Objective
Create `jobspy/search_jobs_ats.py` in `/Users/alex/Github/n1ecC/Jobspy-Custom`. This script runs standalone or in the parallel runner to query native ATS endpoints and output `ats_direct_jobs.csv`.

## Detailed Specifications

### 1. File Path
- Path: `jobspy/search_jobs_ats.py`
- Output Directory: `Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m")`
- Output Filename: `ats_direct_jobs.csv`

### 2. Search Keywords (Identical to `search_jobs_remote.py`)
```python
SEARCH_TERMS = [
    # Core Data & Analytics
    "Data Analyst",
    "Business Analyst",
    "Data Scientist",
    "Statistician",
    "Quantitative Analyst",
    "Operations Research Analyst",
    "Product Analyst",
    "Growth Analyst",
    "Analytics Engineer",

    # Business Intelligence
    "Business Intelligence Analyst",
    "BI Developer",
    "BI Engineer",

    # Domain-Specific Analytics
    "Financial Analyst",
    "Market Research Analyst",
    "Marketing Data Analyst",
    "Marketing Analytics Specialist",
    "Supply Chain Analyst",
    "Pricing Analyst",
    "Revenue Analyst",
    "Sales Operations Analyst",
    "Risk Analyst",

    # Systems & Consulting
    "Business Analytics Consultant",
    "Computer Systems Analyst",
    "Programmer Analyst",
    "Associate Data & Analytics",
    "Technology Advisory Associate",
    "Strategy Consultant",
]
```

### 3. Target Company Registry
```python
TARGET_COMPANIES = {
    "greenhouse": [
        "stripe",
        "airbnb",
        "databricks",
        "snowflake",
        "figma",
        "doordash",
        "instacart",
        "pinterest",
        "dropbox",
        "reddit",
        "robinhood",
    ],
    "lever": [
        "spotify",
        "eventbrite",
    ],
    "ashby": [
        "openai",
        "anthropic",
        "ramp",
        "notion",
        "perplexity",
        "retool",
        "cursor",
        "linear",
        "vercel",
    ],
}
```

### 4. Scraping Flow
1. **Public/Hosted ATS Dataset Query**:
   For each term in `SEARCH_TERMS`:
   - Call `ats_scrapers.search(query=term, remote=True, limit=50)`
   - Call `ats_scrapers.search(query=term, location="United States", limit=50)`
   - Extract records into standardized dictionary format with `search_keyword=term`.

2. **Direct Company ATS Boards**:
   For each company in `TARGET_COMPANIES`:
   - If ATS is greenhouse: `from ats_scrapers.scrapers import GreenhouseScraper; scraper = GreenhouseScraper(company)`
   - If ATS is lever: `from ats_scrapers.scrapers import LeverScraper; scraper = LeverScraper(company)`
   - If ATS is ashby: `from ats_scrapers.scrapers import AshbyScraper; scraper = AshbyScraper(company)`
   - Fetch jobs: `jobs = scraper.fetch()` (with try/except per company so one failure doesn't halt the rest).
   - Filter jobs matching regex: `r"\b(?:analyst|analytics|data|scientist|statistician|quantitative|bi)\b"` in the job title.
   - Map to standardized dictionary format with `search_keyword="target_company"`.

3. **Standardized Columns**:
   ```python
   {
       "id": ..., # unique string or url hash
       "site": "ats",
       "job_url": ..., # direct apply url
       "job_url_direct": ..., # direct apply url
       "title": ...,
       "company": ...,
       "location": ...,
       "date_posted": ...,
       "is_remote": ...,
       "search_keyword": ...,
   }
   ```

4. **Deduplication and CSV Export**:
   - `df.drop_duplicates(subset=["job_url"], keep="first", inplace=True)`
   - Sort alphabetically by `title`:
     ```python
     df.sort_values(
         by="title",
         key=lambda col: col.astype(str).str.lower(),
         ascending=True,
         na_position="last",
         inplace=True,
     )
     ```
   - Export to CSV:
     ```python
     df.to_csv(
         output_path,
         quoting=csv.QUOTE_NONNUMERIC,
         escapechar="\\",
         index=False,
     )
     ```

### 5. Verification
- Run script in test mode (or standalone) to verify `ats_direct_jobs.csv` is generated with valid rows.
- Commit to git: `git add jobspy/search_jobs_ats.py && git commit -m "feat(jobspy): add search_jobs_ats.py for native ATS scraping"`.
