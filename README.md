# Career Pipeline & Job Application Cockpit

Self-contained job discovery, funnel tracking, and AI application packaging pipeline combining multi-board scraping (**JobSpy** + **ATS Direct Scrapers**), a local **DuckDB + Textual TUI Execution Cockpit**, and bundled **career-ops** 8-artifact dossier & resume/cover-letter tailoring.

---

## Quickstart (Clone & Run)

```bash
git clone https://github.com/ne1cc/career-pipeline.git
cd career-pipeline
./setup.sh
```

`./setup.sh` automatically:
1. Installs `uv` (if not already present) and creates a Python 3.12 `.venv` with all dependencies.
2. Installs Playwright Chromium for pixel-perfect 1-page `.pdf` resume and cover letter rendering.
3. Seeds clean starter configuration templates (`config/evidence/`, `config/career_ops/cv.md`, `config/career_ops/config/profile.yml`, `pipeline_config.json`) without tracking any personal data in git.
4. Initializes the local DuckDB database (`data/cockpit.duckdb`) and compiles base resumes (`resumes/*.pdf` & `*.docx`).
5. Links `bin/cockpit` and `bin/tmux-jobcockpit` into `~/bin` and sources `shell/cockpit.sh` (`cjd`, `cjn`, `cjf`, `jobspy-pipeline`, `jobspy-parallel`).

---

## Personalize With Your Own Resumes & Profile (`cockpit onboard`)

No personal resumes or candidate history are hardcoded in the repository. Bring your own `.pdf`, `.docx`, or `.md` resumes using `cockpit onboard`:

```bash
# Option 1: Import your existing PDF/DOCX/Markdown resume(s)
cockpit onboard --from-resumes ~/path/to/your/resume.pdf

# Option 2: Interactive onboarding wizard (sets name, links, target roles, locations & resumes)
cockpit onboard

# Option 3: Non-interactive CLI flags
cockpit onboard \
  --from-resumes ~/resumes/ \
  --name "Jane Smith" \
  --email "jane@example.com" \
  --location "Seattle, WA" \
  --roles "Software Engineer,Data Engineer,Data Analyst" \
  --locations "seattle,remote,san francisco"

# Recompile Markdown base resumes in config/evidence/base_resumes/*.md -> resumes/*.{pdf,docx}
cockpit build-resumes

# Audit environment, database, prompts, resumes, and LLM readiness
cockpit doctor
```

### User-Layer vs. System-Layer Data Contract

All personal files are gitignored so you can customize freely without leaking PII:

| Path | Layer | Purpose |
|---|---|---|
| `config/career_ops/config/profile.yml` | User (gitignored) | Candidate identity, target roles, comp floor, location rules (seeded from `profile.example.yml`) |
| `config/career_ops/cv.md` | User (gitignored) | Master Markdown CV used by `career-ops` evaluation modes (seeded from `cv.example.md`) |
| `config/evidence/01_master_career_history.yaml` | User (gitignored) | Verified employers, titles, dates, degrees, and bullets |
| `config/evidence/02_project_evidence_bank.yaml` | User (gitignored) | Verified project portfolio, stack, and metrics |
| `config/evidence/base_resumes/*.md` | User (gitignored) | Archetype base resumes compiled into `resumes/*.{pdf,docx}` by `cockpit build-resumes` |
| `config/scoring_rules.local.toml` | User (gitignored) | Local overrides for location boosts, keyword weights, and outreach template |
| `config/career_ops/modes/**` & `templates/**` | System (tracked) | Bundled `career-ops` evaluation prompts (`oferta.md`, `_shared.md`, `_writing.md`, `recruiter-side.md`, `hm-audit.md`) and HTML/YAML templates |

---

## Daily Workflow & Commands

| Command | Description |
|---|---|
| `cockpit` (or `cockpit tui`) | Launch the interactive Textual Job Application Cockpit TUI |
| `cockpit pipeline run` | Run the parallel JobSpy + ATS scraper pipeline, build the Excel report, and ingest into DuckDB |
| `cockpit intel` | Generate 8-file application packages (fit matrix, tailored 1-page `.docx`/`.pdf` resume & cover letter, Blocks A–G interview dossier, outreach notes, STAR+T drills) for saved jobs |
| `cockpit dossier [query]` | List saved job dossiers, view in terminal (`--cat`), or open in Neovim (`-v`) |
| `cockpit edit [query]` / `cjn` | Open all 4 core dossier Markdown files in Neovim tabs |
| `cockpit folder [query]` / `cjf` | Open the job's artifact package folder in Finder / file manager |
| `cjd [query]` | `cd` directly into a saved job's dossier folder |
| `cockpit doctor` | Run full healthcheck across DuckDB, prompts, resumes, and LLM provider |

**Additional Docs:** [Shortcuts Guide](docs/COCKPIT_SHORTCUTS.md) | [macOS LaunchAgent Setup](SETUP_MACOS.md) | [Proxies](PROXIES.md)

---

<img src="https://github.com/cullenwatson/JobSpy/assets/78247585/ae185b7e-e444-4712-8bb9-fa97f53e896b" width="400">

**JobSpy** is a job scraping library with the goal of aggregating all the jobs from popular job boards with one tool.

## Features

- Scrapes job postings from **LinkedIn**, **Indeed**, **Glassdoor**, **Google**, **ZipRecruiter**, & other job boards concurrently
- Aggregates the job postings in a dataframe
- Proxies support to bypass blocking

![jobspy](https://github.com/cullenwatson/JobSpy/assets/78247585/ec7ef355-05f6-4fd3-8161-a817e31c5c57)

### Installation

```
pip install -U python-jobspy
```

_Python version >= [3.10](https://www.python.org/downloads/release/python-3100/) required_

### Usage

```python
import csv
from jobspy import scrape_jobs

jobs = scrape_jobs(
    site_name=["indeed", "linkedin", "zip_recruiter", "google"], # "glassdoor", "bayt", "naukri", "bdjobs"
    search_term="software engineer",
    google_search_term="software engineer jobs near San Francisco, CA since yesterday",
    location="San Francisco, CA",
    results_wanted=20,
    hours_old=72,
    country_indeed='USA',
    
    # linkedin_fetch_description=True # gets more info such as description, direct job url (slower)
    # proxies=["208.195.175.46:65095", "208.195.175.45:65095", "localhost"],
)
print(f"Found {len(jobs)} jobs")
print(jobs.head())
jobs.to_csv("jobs.csv", quoting=csv.QUOTE_NONNUMERIC, escapechar="\\", index=False) # to_excel
```

### Output

```
SITE           TITLE                             COMPANY           CITY          STATE  JOB_TYPE  INTERVAL  MIN_AMOUNT  MAX_AMOUNT  JOB_URL                                            DESCRIPTION
indeed         Software Engineer                 AMERICAN SYSTEMS  Arlington     VA     None      yearly    200000      150000      https://www.indeed.com/viewjob?jk=5e409e577046...  THIS POSITION COMES WITH A 10K SIGNING BONUS!...
indeed         Senior Software Engineer          TherapyNotes.com  Philadelphia  PA     fulltime  yearly    135000      110000      https://www.indeed.com/viewjob?jk=da39574a40cb...  About Us TherapyNotes is the national leader i...
linkedin       Software Engineer - Early Career  Lockheed Martin   Sunnyvale     CA     fulltime  yearly    None        None        https://www.linkedin.com/jobs/view/3693012711      Description:By bringing together people that u...
linkedin       Full-Stack Software Engineer      Rain              New York      NY     fulltime  yearly    None        None        https://www.linkedin.com/jobs/view/3696158877      Rain’s mission is to create the fastest and ea...
zip_recruiter Software Engineer - New Grad       ZipRecruiter      Santa Monica  CA     fulltime  yearly    130000      150000      https://www.ziprecruiter.com/jobs/ziprecruiter...  We offer a hybrid work environment. Most US-ba...
zip_recruiter Software Developer                 TEKsystems        Phoenix       AZ     fulltime  hourly    65          75          https://www.ziprecruiter.com/jobs/teksystems-0...  Top Skills' Details• 6 years of Java developme...

```

### Parameters for `scrape_jobs()`

```plaintext
Optional
├── site_name (list|str): 
|    linkedin, zip_recruiter, indeed, glassdoor, google, bayt, bdjobs
|    (default is all)
│
├── search_term (str)
|
├── google_search_term (str)
|     search term for google jobs. This is the only param for filtering google jobs.
│
├── location (str)
│
├── distance (int): 
|    in miles, default 50
│
├── job_type (str): 
|    fulltime, parttime, internship, contract
│
├── proxies (list): 
|    in format ['user:pass@host:port', 'localhost']
|    each job board scraper will round robin through the proxies
|
├── is_remote (bool)
│
├── results_wanted (int): 
|    number of job results to retrieve for each site specified in 'site_name'
│
├── easy_apply (bool): 
|    filters for jobs that are hosted on the job board site (LinkedIn easy apply filter no longer works)
|
├── user_agent (str): 
|    override the default user agent which may be outdated
│
├── description_format (str): 
|    markdown, html (Format type of the job descriptions. Default is markdown.)
│
├── offset (int): 
|    starts the search from an offset (e.g. 25 will start the search from the 25th result)
│
├── hours_old (int): 
|    filters jobs by the number of hours since the job was posted 
|    (ZipRecruiter and Glassdoor round up to next day.)
│
├── verbose (int) {0, 1, 2}: 
|    Controls the verbosity of the runtime printouts 
|    (0 prints only errors, 1 is errors+warnings, 2 is all logs. Default is 2.)

├── linkedin_fetch_description (bool): 
|    fetches full description and direct job url for LinkedIn (Increases requests by O(n))
│
├── linkedin_company_ids (list[int]): 
|    searches for linkedin jobs with specific company ids
|
├── country_indeed (str): 
|    filters the country on Indeed & Glassdoor (see below for correct spelling)
|
├── enforce_annual_salary (bool): 
|    converts wages to annual salary
|
├── ca_cert (str)
|    path to CA Certificate file for proxies
```

```
├── Indeed limitations:
|    Only one from this list can be used in a search:
|    - hours_old
|    - job_type & is_remote
|    - easy_apply
│
└── LinkedIn limitations:
|    Only one from this list can be used in a search:
|    - hours_old
|    - easy_apply
```

## Supported Countries for Job Searching

### **LinkedIn**

LinkedIn searches globally & uses only the `location` parameter. 

### **ZipRecruiter**

ZipRecruiter searches for jobs in **US/Canada** & uses only the `location` parameter.

### **Indeed / Glassdoor**

Indeed & Glassdoor supports most countries, but the `country_indeed` parameter is required. Additionally, use the `location`
parameter to narrow down the location, e.g. city & state if necessary. 

You can specify the following countries when searching on Indeed (use the exact name, * indicates support for Glassdoor):

|                      |              |            |                |
|----------------------|--------------|------------|----------------|
| Argentina            | Australia*   | Austria*   | Bahrain        |
| Belgium*             | Brazil*      | Canada*    | Chile          |
| China                | Colombia     | Costa Rica | Czech Republic |
| Denmark              | Ecuador      | Egypt      | Finland        |
| France*              | Germany*     | Greece     | Hong Kong*     |
| Hungary              | India*       | Indonesia  | Ireland*       |
| Israel               | Italy*       | Japan      | Kuwait         |
| Luxembourg           | Malaysia     | Mexico*    | Morocco        |
| Netherlands*         | New Zealand* | Nigeria    | Norway         |
| Oman                 | Pakistan     | Panama     | Peru           |
| Philippines          | Poland       | Portugal   | Qatar          |
| Romania              | Saudi Arabia | Singapore* | South Africa   |
| South Korea          | Spain*       | Sweden     | Switzerland*   |
| Taiwan               | Thailand     | Turkey     | Ukraine        |
| United Arab Emirates | UK*          | USA*       | Uruguay        |
| Venezuela            | Vietnam*     |            |                |

### **Bayt**

Bayt only uses the search_term parameter currently and searches internationally

## Custom job collection

The daily report gathers jobs posted within the last 14 days across the existing JobSpy searches and ATS boards. West Coast searches include Seattle, San Francisco, Los Angeles, and Portland, plus eligible US remote roles.

The portal scraper adds Dice, We Work Remotely, Hacker News Jobs, and Python.org Jobs alongside Built In, Himalayas, Remotive, Jobicy, RemoteOK, and Arbeitnow. The separate **Last 72 Hours** workbook tab prioritizes verified publication timestamps across both software/security and data/business roles. Results without reliable dates are omitted from that tab. Original post dates are kept separate from when the scraper first discovered a listing.

To monitor the faster sources without rebuilding or emailing the workbook, run:

```sh
poetry run python jobspy/search_jobs_portals.py --watch --poll-interval 600
```

Watch mode updates `jobspy/scraped jobs/results/YYYY_MM/tech_portals_jobs.csv` and `.pipeline_state/portal_first_seen.json`. It starts a pass immediately, then waits ten minutes between passes. Remotive's API has a 24-hour delay; Jobicy documents a three-hour delay and asks integrations not to poll more than hourly; Himalayas refreshes data daily. Those providers remain in the regular collection pass and are not polled in watch mode. Board caching and feed publication cadence still control detection time.

The entry-level priority views exclude explicit senior/lead/management titles. Roles requiring three or more years are labeled as stretch matches where they appear; missing experience requirements remain unknown. Analytics and software/security receive equal base priority.



## Notes
* Indeed is the best scraper currently with no rate limiting.  
* All the job board endpoints are capped at around 1000 jobs on a given search.  
* LinkedIn is the most restrictive and usually rate limits around the 10th page with one ip. Proxies are a must basically.

## Frequently Asked Questions

---
**Q: Why is Indeed giving unrelated roles?**  
**A:** Indeed searches the description too.

- use - to remove words
- "" for exact match

Example of a good Indeed query

```py
search_term='"engineering intern" software summer (java OR python OR c++) 2025 -tax -marketing'
```

This searches the description/title and must include software, summer, 2025, one of the languages, engineering intern exactly, no tax, no marketing.

---

**Q: No results when using "google"?**  
**A:** You have to use super specific syntax. Search for google jobs on your browser and then whatever pops up in the google jobs search box after applying some filters is what you need to copy & paste into the google_search_term. 

---

**Q: Received a response code 429?**  
**A:** This indicates that you have been blocked by the job board site for sending too many requests. All of the job board sites are aggressive with blocking. We recommend:

- Wait some time between scrapes (site-dependent).
- Try using the proxies param to change your IP address.

---

### JobPost Schema

```plaintext
JobPost
├── title
├── company
├── company_url
├── job_url
├── location
│   ├── country
│   ├── city
│   ├── state
├── is_remote
├── description
├── job_type: fulltime, parttime, internship, contract
├── job_function
│   ├── interval: yearly, monthly, weekly, daily, hourly
│   ├── min_amount
│   ├── max_amount
│   ├── currency
│   └── salary_source: direct_data, description (parsed from posting)
├── date_posted
└── emails

Linkedin specific
└── job_level

Linkedin & Indeed specific
└── company_industry

Indeed specific
├── company_country
├── company_addresses
├── company_employees_label
├── company_revenue_label
├── company_description
└── company_logo

Naukri specific
├── skills
├── experience_range
├── company_rating
├── company_reviews_count
├── vacancy_count
└── work_from_home_type
```
