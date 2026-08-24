from __future__ import annotations

from typing import List, Tuple
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from jobspy.util import create_session

log = logging.getLogger("JobSpy:ProxyUtils")


def load_proxies_from_file(path: str) -> List[str]:
    """Load proxy lines from a file, ignoring empty lines and comments."""
    proxies: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                proxies.append(line)
    except FileNotFoundError:
        log.warning("Proxy file not found: %s", path)
    return proxies


def test_proxy(proxy: str, url: str = "https://www.glassdoor.com/Job/computer-science-jobs.htm", timeout: int = 15) -> Tuple[str, int, bool, str]:
    """Test a single proxy by making a GET request to the given URL.

    Returns a tuple: (proxy, status_code_or_0, ok_bool, short_message)
    """
    try:
        session = create_session(proxies=[proxy], is_tls=True, has_retry=False)
        resp = session.get(url, timeout=timeout)
        return proxy, getattr(resp, "status_code", 0), getattr(resp, "ok", False), resp.reason if hasattr(resp, "reason") else ""
    except Exception as exc:
        return proxy, 0, False, str(exc)


def test_proxies(proxies: List[str], max_workers: int = 10) -> List[Tuple[str, int, bool, str]]:
    results = []
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(proxies)))) as executor:
        future_to_proxy = {executor.submit(test_proxy, p): p for p in proxies}
        for future in as_completed(future_to_proxy):
            try:
                results.append(future.result())
            except Exception as exc:
                p = future_to_proxy[future]
                results.append((p, 0, False, f"exception: {exc}"))
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "proxies.example.txt"
    proxies = load_proxies_from_file(path)
    if not proxies:
        print("No proxies found in", path)
        sys.exit(1)
    res = test_proxies(proxies, max_workers=10)
    for p, code, ok, msg in res:
        print(f"{p}\t{code}\t{'OK' if ok else 'FAILED'}\t{msg}")
