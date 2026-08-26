Proxy rotation and testing

JobSpy already supports rotating proxies via jobspy.util.create_session and the RotatingProxySession/TLSRotating classes.

How to use

- Create a file named `proxies.txt` (or use `proxies.example.txt`) with one proxy per line. Supported formats: `http://user:pass@host:port`, `https://host:port`, `socks5://host:port`.
- Run `python scripts/test_proxies.py proxies.txt` to validate which proxies can reach Glassdoor.
- Pass the proxy list into scrape_jobs: e.g. `scrape_jobs(..., proxies=["http://host:port", "http://host2:port"])`.

Recommendations to avoid 400/429/blocked responses on Glassdoor

- Use residential or high-quality proxy providers; datacenter IPs are often blocked.
- Rotate User-Agent strings per request (JobSpy supports passing user_agent). Supply a list and randomize between requests.
- Add delays and exponential backoff between requests; enable retries for transient errors.
- Clear cookies between requests or use fresh sessions when switching IPs (create_session supports clear_cookies via RequestsRotating).
- Use tls-client/tls fingerprints (tls_client) or a headless browser (Playwright/Playwright-stealth) to mimic real browsers for heavy scraping.
- If scraping at scale, respect robots and rate limits; consider official APIs or partnerships.

Limitations

- This repository cannot contain working third-party proxies. Populate your proxies.txt with proxies from your provider.
- For Glassdoor-specific issues (400/403/429), using residential proxies + realistic browser headers + TLS fingerprinting usually helps most.
