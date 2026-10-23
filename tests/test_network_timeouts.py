from types import SimpleNamespace

import pytest
import requests
import tls_client

from jobspy.util import RequestsRotating, TLSRotating


@pytest.mark.parametrize("timeout", [None, 7])
def test_requests_session_has_finite_default_timeout(monkeypatch, timeout):
    forwarded = []

    def request(self, method, url, **kwargs):
        forwarded.append(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(requests.Session, "request", request)
    kwargs = {} if timeout is None else {"timeout": timeout}
    RequestsRotating().get("https://example.com", **kwargs)
    assert forwarded[0]["timeout"] == (60 if timeout is None else 7)


@pytest.mark.parametrize("timeout", [None, 7])
def test_tls_session_has_finite_default_timeout(monkeypatch, timeout):
    forwarded = []

    def request(self, *args, **kwargs):
        forwarded.append(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(tls_client.Session, "execute_request", request)
    kwargs = {} if timeout is None else {"timeout_seconds": timeout}
    TLSRotating().get("https://example.com", **kwargs)
    assert forwarded[0]["timeout_seconds"] == (60 if timeout is None else 7)
