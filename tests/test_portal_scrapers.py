import json
from datetime import datetime, timezone

from jobspy import search_jobs_portals as portals


def test_normalize_portal_job_requires_recent_timestamp_and_target_location():
    assert portals.is_target_location("Portland, OR")
    assert portals.is_target_location("Remote, USA", is_remote=True)
    assert not portals.is_target_location("Remote", is_remote=True)
    assert not portals.is_target_location("Remote, Europe", is_remote=True)
    job = portals.normalize_portal_job(
        "Dice", "1", "Data Analyst", "Acme", "https://jobs.example/1",
        "Seattle, WA", "2026-10-19T10:00:00Z", "Data Analyst",
    )
    assert job["posted_at"] == "2026-10-19T10:00:00+00:00"
    assert portals.normalize_portal_job(
        "Dice", "2", "Data Analyst", "Acme", "https://jobs.example/2",
        "Austin, TX", "2026-10-19T10:00:00Z", "Data Analyst",
    ) is None
    date_only = portals.normalize_portal_job(
        "Dice", "4", "Data Analyst", "Acme", "https://jobs.example/4",
        "Seattle, WA", "2026-10-19", "Data Analyst",
    )
    assert date_only["posted_at"] == ""
    assert date_only["date_posted"] == "2026-10-19"
    assert "description" in date_only
    assert portals.ROLE_REGEX.search("Software Engineers")
    assert portals.ROLE_REGEX.search("Data Analysts")
    assert not portals.ROLE_REGEX.search("Mechanical Hardware Test Engineer")
    assert portals.normalize_portal_job(
        "Dice", "3", "Data Analyst", "Acme", "https://jobs.example/3",
        "Remote, US", "", "Data Analyst",
    ) is None


def test_weworkremotely_feed_keeps_only_matching_us_eligible_roles():
    feed = """<rss><channel>
    <item><guid>1</guid><title>Acme: Data Analyst</title><link>https://weworkremotely.com/remote-jobs/1</link><company>Acme</company><region>USA Only</region><pubDate>Mon, 19 Oct 2026 10:00:00 +0000</pubDate></item>
    <item><guid>2</guid><title>Acme: Data Analyst</title><link>https://weworkremotely.com/remote-jobs/2</link><company>Acme</company><region>Europe Only</region><pubDate>Mon, 19 Oct 2026 10:00:00 +0000</pubDate></item>
    </channel></rss>"""
    result = portals.parse_weworkremotely_feed(feed)
    assert [job["id"] for job in result] == ["we-work-remotely-1"]


def test_hacker_news_requires_role_and_explicit_target_geography():
    base = {"id": 55, "type": "job", "time": 1792404000,
            "title": "Acme is hiring Software Engineers in Seattle", "url": "https://acme.example/jobs"}
    result = portals.parse_hn_job(base)
    assert result["location"] == "Seattle"
    assert portals.parse_hn_job({**base, "title": "Acme is hiring Software Engineers"}) is None
    assert portals.parse_hn_job({**base, "dead": True}) is None


def test_dice_search_page_parser_reads_posted_timestamp_and_rejects_unknown_location():
    jobs = [{"guid": "g1", "title": "Backend Engineer", "companyName": "Acme",
             "detailsPageUrl": "https://dice.com/job-detail/g1", "jobLocation": "Portland, OR",
             "postedDate": "2026-10-19T10:00:00Z"}]
    payload = '"jobList":{"data":' + json.dumps(jobs) + '}'
    page = "<script>self.__next_f.push(" + json.dumps([1, payload]) + ");</script>"
    result = portals.parse_dice_jobs(page)
    assert len(result) == 1
    assert result[0]["title"] == "Backend Engineer"
    assert result[0]["posted_at"] == "2026-10-19T10:00:00+00:00"
    elsewhere = [dict(jobs[0], jobLocation="Austin, TX")]
    payload = '"jobList":{"data":' + json.dumps(elsewhere) + '}'
    page = "<script>self.__next_f.push(" + json.dumps([1, payload]) + ");</script>"
    assert portals.parse_dice_jobs(page) == []


def test_python_jobs_uses_detail_timestamp_when_feed_omits_publication_date():
    feed = """<rss><channel><item><guid>8176</guid><title>Data Analyst, Acme</title>
    <link>https://www.python.org/jobs/8176/</link><description>Remote, Worldwide</description>
    </item></channel></rss>"""
    result = portals.parse_python_jobs_feed(
        feed, detail_loader=lambda url: {"posted_at": "2026-10-19T09:00:00+00:00", "description": "Remote, Worldwide"}
    )
    assert len(result) == 1
    assert result[0]["site"] == "Python.org"
