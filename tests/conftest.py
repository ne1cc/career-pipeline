"""Suite-wide guards: no test may reach a real LLM CLI or pop open a Markdown viewer."""
from __future__ import annotations

import pytest

from cockpit import intel, macos


@pytest.fixture(autouse=True)
def _no_real_dossier_side_effects(monkeypatch):
    def unavailable(*args, **kwargs):
        raise intel.IntelUnavailable("LLM client disabled in tests")

    monkeypatch.setattr(intel, "create_llm_client", unavailable)
    monkeypatch.setattr(macos, "open_markdown", lambda path: None)
