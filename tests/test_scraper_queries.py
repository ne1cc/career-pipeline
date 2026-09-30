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

def test_seattle_search_terms_include_analytics_engineer():
    terms = search_jobs_seattle.search_terms
    assert "Analytics Engineer" in terms
    assert "Business Operations Associate" in terms
    assert "Junior Software Engineer" in terms
    assert "Cybersecurity Analyst" in terms

def test_west_search_terms_include_analytics_engineer():
    terms = search_jobs_west.search_terms
    assert "Analytics Engineer" in terms
    assert "Associate Product Manager" in terms
    assert "Junior Software Engineer" in terms
    assert "Cybersecurity Analyst" in terms

def test_ats_queries_include_early_career_tags():
    terms = search_jobs_ats.DEFAULT_SEARCH_TERMS
    assert any("new grad" in t.lower() or "early career" in t.lower() or "junior" in t.lower() for t in terms)
    assert any("analytics engineer" in t.lower() for t in terms)
    assert any("security" in t.lower() or "cyber" in t.lower() for t in terms)

def test_portals_queries_include_early_career_tags():
    terms = search_jobs_portals.DEFAULT_SEARCH_TERMS
    assert any("new grad" in t.lower() or "early career" in t.lower() or "junior" in t.lower() for t in terms)
    assert any("analytics engineer" in t.lower() for t in terms)
    assert any("security" in t.lower() or "cyber" in t.lower() for t in terms)

