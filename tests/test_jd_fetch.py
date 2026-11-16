from cockpit import jd_fetch

LINKEDIN_GUEST = """<html><body><section class="description">
<div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">
<p>We are hiring a <strong>Data Analyst</strong>.</p><ul><li>SQL</li><li>Python</li></ul>
</div></section></body></html>"""

GREENHOUSE = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "JobPosting", "title": "Analyst",
 "description": "&lt;p&gt;Build &lt;b&gt;Looker&lt;/b&gt; dashboards.&lt;/p&gt;"}
</script></head><body><main>Apply now</main></body></html>"""

GRAPH = """<script type="application/ld+json">{"@graph": [{"@type": "Organization"},
{"@type": ["JobPosting"], "description": "<p>Graph JD</p>"}]}</script>"""


def test_linkedin_id_from_view_urls():
    assert jd_fetch.linkedin_job_id("https://www.linkedin.com/jobs/view/4012345678") == "4012345678"
    assert jd_fetch.linkedin_job_id("https://www.linkedin.com/jobs/view/data-analyst-at-acme-4012345678?trk=x") == "4012345678"
    assert jd_fetch.linkedin_job_id("https://boards.greenhouse.io/acme/jobs/1") is None


def test_parse_linkedin_guest_markup():
    text = jd_fetch.parse_linkedin(LINKEDIN_GUEST)
    assert "**Data Analyst**" in text and "SQL" in text
    assert jd_fetch.parse_linkedin("<html><body>Sign in</body></html>") is None


def test_parse_generic_prefers_json_ld():
    assert jd_fetch.parse_generic(GREENHOUSE) == "Build **Looker** dashboards."
    assert jd_fetch.parse_generic(GRAPH) == "Graph JD"


def test_parse_generic_main_fallback_rejects_shells():
    long_main = "<main><nav>Menu</nav>" + "<p>Responsibilities include analysis.</p>" * 12 + "</main>"
    assert "Responsibilities" in jd_fetch.parse_generic(long_main) and "Menu" not in jd_fetch.parse_generic(long_main)
    assert jd_fetch.parse_generic("<main>Loading…</main>") is None
    assert jd_fetch.parse_generic("<html><body>no main</body></html>") is None


def test_fetch_jd_routes_linkedin_to_guest_endpoint(monkeypatch):
    seen = []
    monkeypatch.setattr(jd_fetch, "_get", lambda url, timeout, retries: seen.append(url) or LINKEDIN_GUEST)
    assert "Data Analyst" in jd_fetch.fetch_jd("https://www.linkedin.com/jobs/view/123")
    assert seen == ["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/123"]
    assert jd_fetch.fetch_jd(None) is None and jd_fetch.fetch_jd("mailto:x") is None


def test_get_retries_429_then_gives_up(monkeypatch):
    class Resp:
        def __init__(self, code):
            self.status_code, self.text = code, "ok"

    codes = iter([429, 503, 200])
    monkeypatch.setattr(jd_fetch.requests, "get", lambda *a, **k: Resp(next(codes)))
    monkeypatch.setattr(jd_fetch.time, "sleep", lambda s: None)
    assert jd_fetch._get("https://x", 1, 2) == "ok"
    monkeypatch.setattr(jd_fetch.requests, "get", lambda *a, **k: Resp(404))
    assert jd_fetch._get("https://x", 1, 2) is None
