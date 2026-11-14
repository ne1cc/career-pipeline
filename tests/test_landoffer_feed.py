"""LandOffer feed: parsing of list and detail pages, request bounds, CSV shape."""

import csv
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "search_jobs_landoffer", Path(__file__).parent.parent / "jobspy" / "search_jobs_landoffer.py"
)
landoffer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(landoffer)


def card(href: str, title: str, sub: str) -> str:
    return (
        f'<div><a class="job-card" href="{href}"><span class="job-card-logo"><img alt="x"/></span>'
        f'<span class="job-card-body"><span class="job-card-title">{title}</span>'
        f'<span class="job-card-sub">{sub}</span></span><span class="job-card-ago">2h ago</span></a></div>'
    )


LIST_PAGE = "<html>" + "".join([
    card("/jobs/acme/data-analyst-101", "Data Analyst", "Acme<!-- --> · <!-- -->Seattle, WA, US"),
    card("/jobs/acme/senior-data-engineer-102", "Senior Data Engineer", "Acme · Remote"),
    card("/jobs/bolt/line-cook-103", "Line Cook", "Bolt · Austin, TX, US"),
    card("/jobs/bolt/data-engineer-ii-104", "Data Engineer II &amp; Analytics", "Bolt · Remote, US"),
]) + "</html>"

DETAIL_PAGE = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"JobPosting","title":"Data Analyst","description":"\\u003cp\\u003eBuild dashboards.\\u003c/p\\u003e\\u003cul\\u003e\\u003cli\\u003eSQL\\u003c/li\\u003e\\u003c/ul\\u003e",
"datePosted":"2026-11-13T05:00:00Z","jobLocation":[{"@type":"Place","address":{"addressLocality":"Seattle","addressRegion":"WA","addressCountry":"US"}}]}]}</script>
<a href="/ai-agent">Let the agent apply</a>
<a class="btn" href="https://jobs.acme.example/apply/101?src=a&amp;b=1"><span>Apply now</span></a></html>"""


def test_parse_cards_and_title_filter():
    cards = landoffer.parse_cards(LIST_PAGE)
    assert cards[0] == {"href": "/jobs/acme/data-analyst-101", "title": "Data Analyst", "company": "Acme", "location": "Seattle, WA, US"}
    assert cards[3]["title"] == "Data Engineer II & Analytics"
    assert [landoffer.wanted(c["title"]) for c in cards] == [True, False, False, True]


def test_parse_detail_reads_posting_and_employer_apply_link():
    detail = landoffer.parse_detail(DETAIL_PAGE)
    assert detail == {
        "description": "Build dashboards.\n- SQL",
        "posted_at": "2026-11-13T05:00:00Z",
        "location": "Seattle, WA, US",
        "apply_url": "https://jobs.acme.example/apply/101?src=a&b=1",
    }
    assert landoffer.parse_detail("<html>redesigned</html>")["apply_url"] == ""


def test_scrape_bounds_requests_and_writes_standard_csv(tmp_path):
    requested = []

    def get(url: str) -> str:
        requested.append(url)
        if "/jobs?" in url:
            return LIST_PAGE
        if url.endswith("-104"):
            raise OSError("boom")  # a failed detail page keeps the list-page fields
        return DETAIL_PAGE

    rows = landoffer.scrape(queries=["data analyst", "data engineer"], hours=24, get=get, delay=0)
    # Short list page (4 < 24) ends each query after page 1; jobs are fetched once across queries.
    assert len([u for u in requested if "/jobs?" in u]) == 2
    assert "posted_since=24" in requested[0] and "/api/" not in "".join(requested)
    assert [r["id"] for r in rows] == ["landoffer_101", "landoffer_104"]
    first, second = rows
    assert first["site"] == "landoffer" and first["job_url"] == "https://landoffer.ai/jobs/acme/data-analyst-101"
    assert first["job_url_direct"] == "https://jobs.acme.example/apply/101?src=a&b=1"
    assert first["date_posted"] == "2026-11-13" and first["description"].startswith("Build dashboards.")
    assert second["job_url_direct"] == second["job_url"] and second["is_remote"] is True and second["description"] == ""

    capped = landoffer.scrape(queries=["data analyst"], get=get, delay=0, max_details=0)
    assert len(capped) == 2 and all(r["description"] == "" for r in capped)
    assert landoffer.scrape(queries=["data analyst"], get=get, delay=0, budget=-1) == []

    path = landoffer.write_csv(rows, tmp_path)
    with open(path, newline="", encoding="utf-8") as f:
        read = list(csv.DictReader(f))
    assert list(read[0].keys()) == landoffer.STANDARD_COLUMNS and len(read) == 2
    assert not landoffer.write_csv([], tmp_path / "empty").exists()
