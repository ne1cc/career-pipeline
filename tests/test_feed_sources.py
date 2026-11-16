from datetime import datetime, timezone

from jobspy import search_jobs_feeds as feeds

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def _parse(adapter, rec, **kw):
    return feeds.parse_source("src", [rec], adapter, now=NOW, **kw)


def test_simplify_keeps_active_recent_target_roles_only():
    base = {"id": "1", "title": "Software Engineer", "company_name": "Acme", "url": "https://a.example/1",
            "locations": ["Seattle, WA"], "active": True, "is_visible": True,
            "date_posted": int(datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp())}
    assert len(_parse(feeds._simplify, base)) == 1
    assert not _parse(feeds._simplify, {**base, "active": False})
    assert not _parse(feeds._simplify, {**base, "locations": ["Austin, TX"]})
    assert not _parse(feeds._simplify, {**base, "date_posted": int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())})


def test_open_jobs_data_remote_flag_becomes_location_text():
    rec = {"jobId": "9", "title": "Data Analyst", "company": "Acme", "applyUrl": "https://a.example/9",
           "locations": ["United States"], "isRemote": True, "postedAt": "2026-11-04T10:00:00-04:00"}
    [job] = _parse(feeds._open_jobs_data, rec)
    assert job["is_remote"] is True and job["site"] == "src"
    assert not _parse(feeds._open_jobs_data, {**rec, "isRemote": None})


def test_applyguy_prefers_direct_listing_url_and_date_only_posted():
    rec = {"id": "x", "title": "Data Engineer", "company": "Acme", "location": "Portland, OR",
           "posted": "2026-11-05", "url": "https://applyguy.ai/x", "listingUrl": "https://acme.example/job"}
    [job] = _parse(feeds._applyguy, rec)
    assert job["job_url"] == "https://acme.example/job" and job["posted_at"] == ""


def test_headcount_multi_location_and_remote_flag():
    rec = {"job_id": "h1", "title": "Software Engineer Intern", "company": "Acme",
           "locations": "Austin, TX; Seattle, WA", "remote": "Hybrid", "posted": "2026-11-04",
           "apply_url": "https://acme.example/h1"}
    [job] = _parse(feeds._headcount, rec)
    assert job["job_url"] == "https://acme.example/h1" and job["is_remote"] is False
    assert not _parse(feeds._headcount, {**rec, "locations": "Austin, TX"})


def test_riley_keeps_only_entry_and_intern_rows():
    rec = {"title": "Data Analyst", "company": "Acme", "location": "Seattle WA", "remote": False,
           "url": "https://jobs.lever.co/acme/1", "skill_level": "entry", "scraped_at": "2026-11-05T09:45:21Z"}
    assert len(_parse(feeds._riley, rec)) == 1
    assert not _parse(feeds._riley, {**rec, "skill_level": "senior"})
