# tests/test_scraper_queries.py
import pytest
from jobspy import search_jobs_remote, search_jobs_seattle, search_jobs_west, search_jobs_ats, search_jobs_portals

def test_remote_search_terms_include_analytics_engineer():
    terms = search_jobs_remote.search_terms
    assert "Analytics Engineer" in terms
    assert "Full Stack Analytics Engineer" in terms
    assert "Associate Product Manager" in terms
    assert "Associate Software Engineer" in terms
    assert "Junior Software Engineer" in terms
    assert "Cybersecurity Analyst" in terms
    assert "Junior Security Engineer" in terms
    assert "Operations Coordinator" in terms
    assert "Associate Analyst" in terms
    assert "Rotational Analyst" in terms

def test_seattle_search_terms_include_analytics_engineer():
    terms = search_jobs_seattle.search_terms
    assert "Analytics Engineer" in terms
    assert "Business Operations Associate" in terms
    assert "Junior Software Engineer" in terms
    assert "Cybersecurity Analyst" in terms
    assert "Operations Coordinator" in terms
    assert "Associate Analyst" in terms

def test_west_search_terms_include_analytics_engineer():
    terms = search_jobs_west.search_terms
    assert "Analytics Engineer" in terms
    assert "Associate Product Manager" in terms
    assert "Junior Software Engineer" in terms
    assert "Cybersecurity Analyst" in terms
    assert "Operations Coordinator" in terms
    assert "Associate Analyst" in terms

def test_ats_queries_include_early_career_tags():
    terms = search_jobs_ats.DEFAULT_SEARCH_TERMS
    assert any("new grad" in t.lower() or "early career" in t.lower() or "junior" in t.lower() for t in terms)
    assert any("analytics engineer" in t.lower() for t in terms)
    assert any("security" in t.lower() or "cyber" in t.lower() for t in terms)
    assert search_jobs_ats.ROLE_PATTERN.search("Software Engineer")
    assert search_jobs_ats.ROLE_PATTERN.search("Backend Platform Engineer")
    assert search_jobs_ats.ROLE_PATTERN.search("Security Analyst")
    assert "Data Engineer" in terms
    assert "Junior Backend Engineer" in terms
    assert "Platform Engineer" in terms

def test_portals_queries_include_early_career_tags():
    terms = search_jobs_portals.DEFAULT_SEARCH_TERMS
    assert any("new grad" in t.lower() or "early career" in t.lower() or "junior" in t.lower() for t in terms)
    assert any("analytics engineer" in t.lower() for t in terms)
    assert any("security" in t.lower() or "cyber" in t.lower() for t in terms)


def test_role_taxonomy_exports_startups_and_pivot_roles():
    from jobspy import role_taxonomy

    assert "ycombinator" in role_taxonomy.STARTUP_HOSTED_ATS_TARGETS
    assert "wellfound" in role_taxonomy.STARTUP_HOSTED_ATS_TARGETS
    assert "ashby" in role_taxonomy.DEFAULT_HOSTED_ATS_TARGETS
    assert "Forward Deployed Engineer" in role_taxonomy.PIVOT_AND_STEPPING_STONE_TERMS
    assert "Implementation Consultant" in role_taxonomy.PIVOT_AND_STEPPING_STONE_TERMS
    assert "Product Operations Analyst" in role_taxonomy.PIVOT_AND_STEPPING_STONE_TERMS


def test_ats_includes_startups_and_stepping_stone_patterns():
    assert "gem" in search_jobs_ats.TARGET_COMPANIES
    all_slugs = {
        slug
        for slugs in search_jobs_ats.TARGET_COMPANIES.values()
        for slug in slugs
    }
    for startup in ("posthog", "motherduck", "baseten", "vanta", "metabase", "planetscale"):
        assert startup in all_slugs

    pivot_titles = [
        "Forward Deployed Engineer",
        "Implementation Consultant",
        "Solutions Engineer",
        "Technical Support Engineer",
        "Product Operations Analyst",
        "ETL Developer",
        "Founder's Associate",
    ]
    for title in pivot_titles:
        assert search_jobs_ats.ROLE_PATTERN.search(title), f"ATS ROLE_PATTERN missed {title}"
        assert search_jobs_portals.ROLE_REGEX.search(title), f"Portal ROLE_REGEX missed {title}"

    assert not search_jobs_portals.ROLE_REGEX.search("Mechanical Hardware Test Engineer")


def test_ats_include_all_roles_mode(monkeypatch):
    from types import SimpleNamespace
    import pandas as pd

    fake_jobs = [
        SimpleNamespace(
            title="Chief of Staff",
            company="PostHog",
            location="Remote, US",
            url="https://jobs.ashbyhq.com/posthog/1",
            apply_url="https://jobs.ashbyhq.com/posthog/1",
            global_id="g1",
            ats_id="a1",
            posted_at="2026-10-19T00:00:00Z",
            is_remote=True,
        ),
        SimpleNamespace(
            title="Account Executive",
            company="PostHog",
            location="Remote, US",
            url="https://jobs.ashbyhq.com/posthog/2",
            apply_url="https://jobs.ashbyhq.com/posthog/2",
            global_id="g2",
            ats_id="a2",
            posted_at="2026-10-19T00:00:00Z",
            is_remote=True,
        ),
    ]

    class FakeScraper:
        def fetch(self):
            return fake_jobs

    monkeypatch.setattr(search_jobs_ats, "get_scraper", lambda ats, slug: FakeScraper())
    filtered = search_jobs_ats.scrape_target_companies({"ashby": ["posthog"]}, include_all_roles=False)
    assert [r["title"] for r in filtered] == ["Chief of Staff"]

    broad = search_jobs_ats.scrape_target_companies({"ashby": ["posthog"]}, include_all_roles=True)
    assert {r["title"] for r in broad} == {"Chief of Staff", "Account Executive"}

    fake_df = pd.DataFrame([
        {
            "title": "Forward Deployed Engineer",
            "company": "Startup",
            "location": "Remote, US",
            "url": "https://example.com/fde",
            "apply_url": "https://example.com/fde",
            "global_id": "d1",
            "ats_id": "d1",
            "posted_at": "2026-10-19T00:00:00Z",
            "is_remote": True,
        },
        {
            "title": "Office Manager",
            "company": "Startup",
            "location": "Remote, US",
            "url": "https://example.com/om",
            "apply_url": "https://example.com/om",
            "global_id": "d2",
            "ats_id": "d2",
            "posted_at": "2026-10-19T00:00:00Z",
            "is_remote": True,
        },
    ])

    class FakeClient:
        def load(self, ats=None):
            return fake_df

    monkeypatch.setattr(search_jobs_ats, "Client", FakeClient)
    ds_broad = search_jobs_ats.scrape_hosted_dataset(
        ["Data Analyst"],
        ats_targets=["ycombinator"],
        include_all_roles=True,
    )
    assert [r["title"] for r in ds_broad] == ["Forward Deployed Engineer"]


