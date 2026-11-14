"""Shared startup ATS targets and stepping-stone / pivot role taxonomy.

Intent: centralize smaller/growth-stage startup ATS slugs, startup-friendly hosted
snapshot targets, and bridge/stepping-stone role titles so scrapers pull from
startups and step-up paths without duplicating lists across scripts.

Contract:
- Exports immutable-style module constants (`PIVOT_AND_STEPPING_STONE_TERMS`,
  `HIGH_YIELD_PIVOT_TERMS`, `STARTUP_TARGET_COMPANIES`,
  `STARTUP_HOSTED_ATS_TARGETS`, `DEFAULT_HOSTED_ATS_TARGETS`).
- `merge_terms(*lists)` returns a deduplicated list preserving first-seen order.
- `merge_company_maps(*maps)` merges ATS -> slug lists without duplicates.

Limits:
- `HIGH_YIELD_PIVOT_TERMS` is intentionally bounded (~19 terms) to avoid query-count
  timeouts in rate-limited LinkedIn/Indeed scrapers (`search_jobs_*.py`).
"""

from __future__ import annotations

from typing import Dict, Iterable, List

# Curated bridge, stepping-stone, and step-up roles across 5 pivot tracks.
PIVOT_AND_STEPPING_STONE_TERMS: List[str] = [
    # Track 1: Field, Solutions & Deployment Engineering (Bridge -> Data Eng, SWE, PM)
    "Forward Deployed Engineer",
    "Forward Deployed Software Engineer",
    "Deployment Strategist",
    "Implementation Engineer",
    "Implementation Consultant",
    "Implementation Specialist",
    "Solutions Engineer",
    "Associate Solutions Engineer",
    "Sales Engineer",
    "Customer Engineer",
    "Partner Engineer",
    "Integration Engineer",
    "Technical Account Manager",
    "Customer Success Engineer",
    "Client Solutions Analyst",
    "Onboarding Specialist",
    # Track 2: Technical Support, QA & Automation (Bridge -> SWE, Platform/DevOps, Data Eng)
    "Technical Support Engineer",
    "Support Engineer",
    "Application Support Engineer",
    "Product Support Engineer",
    "Developer Support Engineer",
    "API Support Specialist",
    "QA Engineer",
    "Quality Assurance Engineer",
    "QA Analyst",
    "Test Automation Engineer",
    "SDET",
    "Software Engineer in Test",
    "Cloud Support Engineer",
    "Systems Administrator",
    "Automation Engineer",
    # Track 3: Data Operations, ETL, Governance & Reporting (Bridge -> Analytics Eng, Data Eng)
    "Data Operations Analyst",
    "Data Operations Specialist",
    "Data Specialist",
    "Data Coordinator",
    "Data Quality Analyst",
    "Data Governance Analyst",
    "ETL Developer",
    "SQL Developer",
    "Database Analyst",
    "Reporting Specialist",
    "BI Associate",
    "Business Intelligence Specialist",
    "Analytics Specialist",
    "Analytics Associate",
    "Data Integration Specialist",
    "Data Migration Specialist",
    # Track 4: Product Ops, Growth & Technical Delivery (Bridge -> Product Analyst, APM/PM)
    "Product Operations",
    "Product Operations Analyst",
    "Product Operations Specialist",
    "Product Specialist",
    "Product Analytics Engineer",
    "Growth Operations",
    "Growth Engineer",
    "Experimentation Analyst",
    "Conversion Rate Optimization",
    "Technical Project Manager",
    "Technical Program Manager",
    "Associate Program Manager",
    "Delivery Analyst",
    # Track 5: Startup Generalist, TechOps, AI & Full-Stack
    "Founder's Associate",
    "Founders Associate",
    "Special Projects",
    "Operations Associate",
    "Strategy Associate",
    "TechOps Analyst",
    "Technical Operations Analyst",
    "Systems Operations",
    "Customer Operations Analyst",
    "Marketplace Operations",
    "Deal Desk Analyst",
    "Full Stack Engineer",
    "Full-Stack Engineer",
    "AI Engineer",
    "Applied AI Engineer",
    "Machine Learning Engineer",
    "Founding Engineer",
]

# Bounded subset for rate-limited LinkedIn/Indeed scrapers.
HIGH_YIELD_PIVOT_TERMS: List[str] = [
    "Forward Deployed Engineer",
    "Implementation Engineer",
    "Implementation Consultant",
    "Solutions Engineer",
    "Associate Solutions Engineer",
    "Technical Account Manager",
    "Customer Success Engineer",
    "Technical Support Engineer",
    "Support Engineer",
    "QA Engineer",
    "Test Automation Engineer",
    "ETL Developer",
    "SQL Developer",
    "Product Operations Analyst",
    "Technical Project Manager",
    "Technical Program Manager",
    "Founder's Associate",
    "Full Stack Engineer",
    "AI Engineer",
]

# Verified smaller & growth-stage startup slugs from ats_scrapers directory.
STARTUP_TARGET_COMPANIES: Dict[str, List[str]] = {
    "ashby": [
        "airbyte",
        "anomalo",
        "ashby",
        "attio",
        "baseten",
        "braintrust",
        "browserbase",
        "campfire",
        "cerebras",
        "convex-dev",
        "decagon",
        "e2b",
        "elevenlabs",
        "exa",
        "firecrawl",
        "fireworks",
        "graphite",
        "harvey",
        "hightouch",
        "inngest",
        "langfuse",
        "lightdash",
        "mem0",
        "montecarlodata",
        "motherduck",
        "n8n",
        "neon",
        "numeric",
        "omni",
        "plain",
        "poolside",
        "posthog",
        "pylon",
        "railway",
        "render",
        "rillet",
        "sierra",
        "unstructured",
        "vanta",
    ],
    "greenhouse": [
        "planetscale",
        "sourcegraph91",
        "tavily",
    ],
    "lever": [
        "metabase",
        "tinybird",
    ],
    "gem": [
        "groq",
        "retool",
        "zep",
        "11x-ai",
        "afterquery",
        "agenta-ai",
        "agentnoon",
        "alpharun",
    ],
}

# Startup-heavy hosted ATS snapshot slices in ats_scrapers.Client().
STARTUP_HOSTED_ATS_TARGETS: List[str] = [
    "ycombinator",
    "wellfound",
    "gem",
    "workable",
    "pinpoint",
    "recruiterbox",
    "rippling",
    "bamboohr",
]

# Full hosted ATS snapshot list (startup-heavy slices first, then broad ATS boards).
DEFAULT_HOSTED_ATS_TARGETS: List[str] = [
    *STARTUP_HOSTED_ATS_TARGETS,
    "ashby",
    "greenhouse",
    "lever",
]


def merge_terms(*term_lists: Iterable[str]) -> List[str]:
    """Return deduplicated search terms while preserving first-seen order."""
    seen: set[str] = set()
    merged: List[str] = []
    for terms in term_lists:
        for term in terms:
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(term)
    return merged


def merge_company_maps(*maps: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Merge ATS -> company slug dictionaries without duplicating slugs."""
    merged: Dict[str, List[str]] = {}
    for company_map in maps:
        for ats_type, slugs in company_map.items():
            bucket = merged.setdefault(ats_type, [])
            existing = {s.lower() for s in bucket}
            for slug in slugs:
                if slug.lower() not in existing:
                    existing.add(slug.lower())
                    bucket.append(slug)
    return merged
