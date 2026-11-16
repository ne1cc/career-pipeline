import argparse
import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import ats_scrapers
from ats_scrapers import Client
from ats_scrapers.scrapers import (
    AshbyScraper,
    GemScraper,
    GreenhouseScraper,
    LeverScraper,
    get_scraper,
)

try:
    from jobspy.role_taxonomy import (
        DEFAULT_HOSTED_ATS_TARGETS,
        PIVOT_AND_STEPPING_STONE_TERMS,
        STARTUP_TARGET_COMPANIES,
        merge_company_maps,
        merge_terms,
    )
except ImportError:
    from role_taxonomy import (  # type: ignore[no-redef]
        DEFAULT_HOSTED_ATS_TARGETS,
        PIVOT_AND_STEPPING_STONE_TERMS,
        STARTUP_TARGET_COMPANIES,
        merge_company_maps,
        merge_terms,
    )

# Output directory: jobspy/scraped jobs/results/YYYY_MM/YYYY_MM_DD/
default_out = Path(__file__).parent / "scraped jobs" / "results" / datetime.now().strftime("%Y_%m") / datetime.now().strftime("%Y_%m_%d")
OUTPUT_DIR = Path(os.environ.get("JOBSPY_RESULTS_DIR", default_out))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Expanded Search Keywords
_BASE_SEARCH_TERMS = [
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
    "Full Stack Analytics Engineer",
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
    # Product & Operations
    "Associate Product Manager",
    "Business Operations Associate",
    "BizOps Analyst",
    "RevOps Analyst",
    "Revenue Operations Analyst",
    "Sales Operations Analyst",
    "GTM Operations Analyst",
    "Solutions Analyst",
    "Commercial Analytics Associate",
    "Operations Coordinator",
    "Associate Analyst",
    "Rotational Analyst",
    "Data Operations Specialist",
    # Early Career & Junior Roles
    "New Grad",
    "Early Career",
    "University Grad",
    "New Grad Software Engineer",
    "Software Engineer I",
    "Data Analyst I",
    "Data Engineer",
    "Junior Backend Engineer",
    "Backend Engineer",
    "Platform Engineer",
    "Product Engineer",
    "Associate Product Engineer",
    "Junior Data Analyst",
    "Early Career Analyst",
    "Junior Software Engineer",
    "Associate Software Engineer",
    "Junior Developer",
    "Junior Security Engineer",
    "Cybersecurity Analyst",
    "SOC Analyst",
]
DEFAULT_SEARCH_TERMS = merge_terms(_BASE_SEARCH_TERMS, PIVOT_AND_STEPPING_STONE_TERMS)
SEARCH_TERMS = DEFAULT_SEARCH_TERMS

# Target Company Registry across Greenhouse, Lever, Ashby, Gem
_BASE_TARGET_COMPANIES: Dict[str, List[str]] = {
    "greenhouse": [
        "affirm",
        "airbnb",
        "airtable",
        "andurilindustries",
        "anthropic",
        "asana",
        "blend",
        "brex",
        "carta",
        "checkr",
        "chime",
        "clickhouse",
        "cloudflare",
        "coinbase",
        "dagsterlabs",
        "databricks",
        "datadog",
        "dbtlabsinc",
        "doordashusa",
        "dropbox",
        "elastic",
        "figma",
        "fivetran",
        "gitlab",
        "gleanwork",
        "gusto",
        "hextechnologies",
        "hubspotjobs",
        "instacart",
        "lattice",
        "lyft",
        "marqeta",
        "mongodb",
        "netskope",
        "okta",
        "pagerduty",
        "pinterest",
        "postman",
        "reddit",
        "robinhood",
        "rubrik",
        "samsara",
        "scaleai",
        "spacex",
        "starburst",
        "stripe",
        "tanium",
        "temporal",
        "toast",
        "togetherai",
        "twilio",
        "vercel",
        "webflow",
    ],
    "ashby": [
        "anyscale",
        "astronomer",
        "benchling",
        "character",
        "clickup",
        "cohere",
        "confluent",
        "cursor",
        "cube",
        "deepgram",
        "deel",
        "ironcladhq",
        "langchain",
        "linear",
        "llamaindex",
        "mercury",
        "modal",
        "notion",
        "openai",
        "perplexity",
        "pinecone",
        "plaid",
        "prefect",
        "ramp",
        "runway-ml",
        "sentry",
        "snowflake",
        "snyk",
        "supabase",
        "weaviate",
        "wiz",
        "zapier",
    ],
    "lever": [
        "mistral",
        "palantir",
        "spotify",
    ],
}
TARGET_COMPANIES: Dict[str, List[str]] = merge_company_maps(
    _BASE_TARGET_COMPANIES,
    STARTUP_TARGET_COMPANIES,
)

# Explicit aliases/mappings for company boards that reside on specific slugs or ATS types
COMPANY_ALIASES = {
    "snowflake": ("ashby", "snowflake"),
    "anthropic": ("greenhouse", "anthropic"),
    "doordash": ("greenhouse", "doordashusa"),
    "eventbrite": ("greenhouse", "eventbriteinc"),
    "retool": ("gem", "retool"),
    "hubspot": ("greenhouse", "hubspotjobs"),
    "anduril": ("greenhouse", "andurilindustries"),
    "scale": ("greenhouse", "scaleai"),
    "glean": ("greenhouse", "gleanwork"),
}

# Regex filter for analytics/data/bi/operations/pivot job titles in target company boards
ROLE_PATTERN = re.compile(
    r"\b(?:analyst|analytics|data|scientist|statistician|quantitative|bi|"
    r"software|developer|backend|back-end|platform|product engineer|security|cybersecurity|"
    r"infosec|soc|devops|sre|revops|bizops|operations|solutions|"
    r"forward deployed|deployment strategist|implementation|integration|sales engineer|"
    r"partner engineer|technical account|customer success engineer|support engineer|"
    r"technical support|qa|quality assurance|test automation|sdet|etl|sql developer|"
    r"reporting|data quality|data governance|product operations|product ops|product manager|"
    r"apm|program manager|project manager|project coordinator|founder'?s associate|"
    r"chief of staff|special projects|techops|technical operations|deal desk|"
    r"onboarding specialist|client solutions|full stack|full-stack|frontend|front-end|"
    r"ai engineer|ml engineer|machine learning|automation engineer|founding engineer)\b",
    re.IGNORECASE,
)

# Pre-filter senior/executive titles and clearly non-US locations before saving ATS CSVs
ATS_SENIOR_TITLE_PATTERN = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|director|vp|vice president|"
    r"lead|head of|chief|distinguished|fellow|iii|iv|v|l5|l6|l7|ic4|ic5|ic6)\b",
    re.IGNORECASE,
)

US_LOCATION_PATTERN = re.compile(
    r"(?:\b(?:united states|usa|u\.s\.|remote|seattle|bellevue|redmond|kirkland|renton|issaquah|"
    r"washington|california|san francisco|los angeles|san diego|san jose|palo alto|mountain view|"
    r"sunnyvale|menlo park|oregon|portland|arizona|phoenix|tempe|scottsdale|colorado|denver|boulder|"
    r"texas|austin|dallas|houston|new york|nyc|boston|chicago|atlanta|raleigh|durham|virginia|"
    r"utah|minnesota|florida|michigan|pennsylvania|new jersey|maryland)\b|"
    r"(?:,\s*|\b)(?:wa|ca|az|co|tx|ny|ma|il|ga|nc|va|dc|ut|mn|fl|mi|pa|nj|md)\b|"
    r",\s*or\b|\bus\b)",
    re.IGNORECASE,
)


def is_target_ats_role(title: str, location: str, is_remote: bool) -> bool:
    """Keep US/remote/unspecified-location roles that are not senior/executive titles."""
    if ATS_SENIOR_TITLE_PATTERN.search(title or ""):
        return False
    loc = (location or "").strip()
    if is_remote or not loc:
        return True
    return bool(US_LOCATION_PATTERN.search(loc))

STANDARD_COLUMNS = [
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "posted_at",
    "is_remote",
    "search_keyword",
]


def normalize_job_object(job: Any, search_keyword: str) -> Dict[str, Any]:
    """Normalize an ats_scrapers Job model instance to standard schema."""
    job_url = str(job.url) if job.url else (str(job.apply_url) if job.apply_url else "")
    apply_url = str(job.apply_url) if job.apply_url else job_url
    if not job_url:
        job_url = apply_url

    job_id = getattr(job, "global_id", None) or getattr(job, "ats_id", None) or job_url
    posted_at = str(job.posted_at) if job.posted_at else None

    is_remote = job.is_remote
    if is_remote is None:
        # Infer remote if location mentions remote
        loc_str = str(job.location or "").lower()
        is_remote = "remote" in loc_str

    return {
        "id": str(job_id),
        "site": "ats",
        "job_url": job_url,
        "job_url_direct": apply_url,
        "title": str(job.title or "").strip(),
        "company": str(job.company or "").strip(),
        "location": str(job.location or "").strip(),
        "date_posted": posted_at[:10] if posted_at else None,
        "posted_at": posted_at,
        "is_remote": bool(is_remote),
        "search_keyword": search_keyword,
    }


def normalize_dataset_row(row: pd.Series, search_keyword: str) -> Dict[str, Any]:
    """Normalize a hosted dataset DataFrame row to standard schema."""
    job_url = str(row.get("url", "")) if pd.notna(row.get("url")) else ""
    apply_url = str(row.get("apply_url", "")) if pd.notna(row.get("apply_url")) else job_url
    if not job_url or job_url == "nan":
        job_url = apply_url

    job_id = row.get("global_id") or row.get("ats_id") or job_url
    if pd.isna(job_id) or str(job_id) == "nan":
        job_id = job_url

    posted_at = str(row.get("posted_at")) if pd.notna(row.get("posted_at")) else None
    if posted_at == "nan":
        posted_at = None

    is_remote = row.get("is_remote")
    if pd.isna(is_remote):
        loc_str = str(row.get("location", "")).lower()
        is_remote = "remote" in loc_str
    else:
        is_remote = bool(is_remote)

    title = str(row.get("title", "")) if pd.notna(row.get("title")) else ""
    company = str(row.get("company", "")) if pd.notna(row.get("company")) else ""
    location = str(row.get("location", "")) if pd.notna(row.get("location")) else ""

    return {
        "id": str(job_id),
        "site": "ats",
        "job_url": job_url,
        "job_url_direct": apply_url,
        "title": title.strip(),
        "company": company.strip(),
        "location": location.strip(),
        "date_posted": posted_at[:10] if posted_at else None,
        "posted_at": posted_at,
        "is_remote": is_remote,
        "search_keyword": search_keyword,
    }


def scrape_target_companies(
    target_companies: Dict[str, List[str]],
    include_all_roles: bool = False,
) -> List[Dict[str, Any]]:
    """Query direct company ATS boards and filter for target/pivot roles.

    Contract:
    - Returns normalized job dicts matching STANDARD_COLUMNS.
    - When `include_all_roles` is True, skips `ROLE_PATTERN` title pre-filtering
      so downstream US/seniority filters decide what to keep.
    """
    all_matched_jobs: List[Dict[str, Any]] = []

    print(f"\n[1/2] Scraping direct ATS boards for target companies...")
    for default_ats, companies in target_companies.items():
        for company in companies:
            ats_type, slug = COMPANY_ALIASES.get(company, (default_ats, company))
            print(f" > Fetching '{company}' ({ats_type}:{slug})...", end=" ", flush=True)

            try:
                scraper = get_scraper(ats_type, slug)
                jobs = scraper.fetch()
                if include_all_roles:
                    matched = list(jobs)
                else:
                    matched = [j for j in jobs if ROLE_PATTERN.search(j.title or "")]

                for job in matched:
                    # If job.company is missing, default to company name
                    if not getattr(job, "company", None):
                        job.company = company
                    record = normalize_job_object(
                        job,
                        search_keyword="all_roles" if include_all_roles else "target_company",
                    )
                    all_matched_jobs.append(record)

                print(f"found {len(matched)} matching roles (out of {len(jobs)} total)")
            except Exception as e:
                print(f"error: {e}")

    return all_matched_jobs


def scrape_hosted_dataset(
    search_terms: List[str],
    is_smoke_test: bool = False,
    ats_targets: Optional[List[str]] = None,
    include_all_roles: bool = False,
) -> List[Dict[str, Any]]:
    """Query hosted ATS dataset snapshots (startup + broad ATS slices) for keywords.

    Contract:
    - Returns normalized job dicts across `ats_targets` (defaults to `DEFAULT_HOSTED_ATS_TARGETS`).
    - Pre-filters snapshots in a single vectorized regex pass when scanning > 5 terms.
    """
    all_matched_jobs: List[Dict[str, Any]] = []

    if is_smoke_test:
        print("\n[2/2] Skipping hosted dataset download in smoke-test mode.")
        return all_matched_jobs

    if ats_targets is None:
        ats_targets = list(DEFAULT_HOSTED_ATS_TARGETS)

    client = Client()
    for ats_name in ats_targets:
        print(f"\n[2/2] Loading hosted {ats_name.capitalize()} dataset snapshot...")
        try:
            df_snapshot = client.load(ats=ats_name)
            print(f"Loaded {ats_name} snapshot with {len(df_snapshot)} postings.")
            if df_snapshot.empty or "title" not in df_snapshot.columns:
                continue

            titles = df_snapshot["title"].fillna("").astype(str)

            # 1. Broad-role mode: scan once using ROLE_PATTERN instead of per-term queries.
            if include_all_roles:
                sub_df = df_snapshot[titles.str.contains(ROLE_PATTERN, na=False)]
                broad_records = [
                    normalize_dataset_row(row, search_keyword="broad_role")
                    for _, row in sub_df.iterrows()
                ]
                if broad_records:
                    all_matched_jobs.extend(broad_records)
                    print(f" > broad_role ({ats_name}): found {len(broad_records)} roles")
                continue

            # 2. Pre-filter once across all search terms when term list is large.
            candidate_df = df_snapshot
            if len(search_terms) > 5:
                combined_pattern = "|".join(re.escape(t) for t in search_terms if t)
                if combined_pattern:
                    candidate_df = df_snapshot[
                        titles.str.contains(combined_pattern, case=False, na=False, regex=True)
                    ]
                    if candidate_df.empty:
                        continue

            candidate_titles = candidate_df["title"].fillna("").astype(str)
            for term in search_terms:
                pattern = re.escape(term)
                sub_df = candidate_df[
                    candidate_titles.str.contains(pattern, case=False, na=False, regex=True)
                ]

                term_records = [
                    normalize_dataset_row(row, search_keyword=term)
                    for _, row in sub_df.iterrows()
                ]

                if term_records:
                    all_matched_jobs.extend(term_records)
                    print(f" > '{term}' ({ats_name}): found {len(term_records)} roles")

        except Exception as e:
            print(f"Warning: Failed or interrupted loading hosted dataset for '{ats_name}': {e}")

    return all_matched_jobs


def run_ats_scrape(
    smoke_test: bool = False,
    skip_dataset: bool = False,
    include_all_roles: bool | None = None,
) -> Path:
    """Run native ATS scraping flow and save deduplicated, sorted results to CSV."""
    if include_all_roles is None:
        include_all_roles = os.environ.get("JOBSPY_BROAD_ROLES", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    if smoke_test:
        print("=== RUNNING IN SMOKE-TEST MODE ===")
        terms_to_search = ["Data Analyst"]
        companies_to_scrape = {
            "greenhouse": ["stripe"],
            "ashby": ["openai"],
        }
    else:
        terms_to_search = SEARCH_TERMS
        companies_to_scrape = TARGET_COMPANIES

    all_jobs: List[Dict[str, Any]] = []

    # 1. Scrape direct target companies
    company_jobs = scrape_target_companies(
        companies_to_scrape,
        include_all_roles=include_all_roles,
    )
    all_jobs.extend(company_jobs)

    # 2. Scrape hosted dataset
    if not skip_dataset:
        dataset_jobs = scrape_hosted_dataset(
            terms_to_search,
            is_smoke_test=smoke_test,
            include_all_roles=include_all_roles,
        )
        all_jobs.extend(dataset_jobs)

    if not all_jobs:
        print("\nNo jobs collected.")
        return OUTPUT_DIR / "ats_direct_jobs.csv"

    df = pd.DataFrame(all_jobs)

    # Ensure all standard columns exist
    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[STANDARD_COLUMNS]

    # Deduplicate by job_url
    initial_count = len(df)
    df.drop_duplicates(subset=["job_url"], keep="first", inplace=True)
    deduped_count = len(df)

    # Filter out senior/executive titles and clearly non-US locations
    keep_mask = [
        is_target_ats_role(str(t or ""), str(loc or ""), bool(rem))
        for t, loc, rem in zip(df["title"], df["location"], df["is_remote"])
    ]
    df = df[keep_mask].copy()
    filtered_count = len(df)

    # Sort alphabetically by title (case-insensitive)
    df.sort_values(
        by="title",
        key=lambda col: col.astype(str).str.lower(),
        ascending=True,
        na_position="last",
        inplace=True,
    )

    output_path = OUTPUT_DIR / "ats_direct_jobs.csv"
    df.to_csv(
        output_path,
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
        index=False,
    )

    print(
        f"\nFinished ATS scraping. Extracted {initial_count} roles -> {deduped_count} unique -> {filtered_count} US/non-senior roles."
    )
    print(f"Saved to: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Scrape jobs directly from ATS endpoints and hosted datasets."
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Fast smoke test on a small subset of terms and companies.",
    )
    parser.add_argument(
        "--skip-dataset",
        action="store_true",
        help="Skip hosted dataset snapshot download and only query direct company ATS boards.",
    )
    parser.add_argument(
        "--include-all-roles",
        action="store_true",
        help="Skip title pre-filtering on direct ATS boards and use ROLE_PATTERN across hosted slices (also via JOBSPY_BROAD_ROLES=1).",
    )
    args = parser.parse_args()

    run_ats_scrape(
        smoke_test=args.smoke_test,
        skip_dataset=args.skip_dataset,
        include_all_roles=args.include_all_roles or None,
    )


if __name__ == "__main__":
    main()
