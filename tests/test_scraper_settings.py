# tests/test_scraper_settings.py
import ast
from pathlib import Path
import pytest
from jobspy import search_jobs_msba_west, search_jobs_seattle_msba

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBSPY_DIR = REPO_ROOT / "jobspy"

SCRIPTS_TO_CHECK = [
    JOBSPY_DIR / "search_jobs_remote.py",
    JOBSPY_DIR / "search_jobs_seattle.py",
    JOBSPY_DIR / "search_jobs_seattle_entry_level.py",
    JOBSPY_DIR / "search_jobs_west.py",
    JOBSPY_DIR / "search_jobs_seattle_msba.py",
    JOBSPY_DIR / "search_jobs_msba_west.py",
]


def extract_scrape_jobs_kwargs(file_path: Path) -> list[dict]:
    """Parse python file AST and return kwargs passed to any scrape_jobs calls."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    calls_kwargs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name == "scrape_jobs":
                kwargs = {}
                for kw in node.keywords:
                    if isinstance(kw.value, ast.Constant):
                        kwargs[kw.arg] = kw.value.value
                calls_kwargs.append(kwargs)
    return calls_kwargs


@pytest.mark.parametrize("script_path", SCRIPTS_TO_CHECK)
def test_scraper_hours_old_and_fetch_description(script_path: Path):
    kwargs_list = extract_scrape_jobs_kwargs(script_path)
    assert len(kwargs_list) > 0, f"No scrape_jobs call found in {script_path.name}"
    for kwargs in kwargs_list:
        assert kwargs.get("hours_old", 0) <= 48, f"{script_path.name} has hours_old > 48"
        assert kwargs.get("results_wanted") == 9999, f"{script_path.name} results_wanted != 9999"
        assert kwargs.get("linkedin_fetch_description", False) is False, (
            f"{script_path.name} must not fetch linkedin descriptions"
        )


def test_msba_recruitment_filter_handles_empty_description():
    """Verify MSBA filters accept valid analytics titles even when description is omitted."""
    valid_titles = [
        "Data Analyst",
        "Junior Business Analyst",
        "Analytics Engineer",
        "Product Analyst",
        "Financial Analyst",
        "Operations Research Analyst",
    ]
    for title in valid_titles:
        assert search_jobs_msba_west.recruits_msba_graduates(title, "") is True
        assert search_jobs_msba_west.recruits_msba_graduates(title, None) is True
        assert search_jobs_seattle_msba.recruits_msba_graduates(title, "") is True
        assert search_jobs_seattle_msba.recruits_msba_graduates(title, None) is True

    # Irrelevant non-analytics titles should still be rejected
    invalid_titles = ["Warehouse Loader", "Cashier", "Truck Driver", "Receptionist"]
    for title in invalid_titles:
        assert search_jobs_msba_west.recruits_msba_graduates(title, "") is False
        assert search_jobs_seattle_msba.recruits_msba_graduates(title, "") is False


def test_msba_recruitment_filter_preserves_description_matches():
    """Verify MSBA filters continue to match explicit degree criteria when descriptions exist."""
    desc = "Applicants should possess a master's degree in analytics or statistics."
    assert search_jobs_msba_west.recruits_msba_graduates("General Consultant", desc) is True
    assert search_jobs_seattle_msba.recruits_msba_graduates("Associate", desc) is True


def test_is_entry_to_mid_level_filters_senior_roles():
    """Verify senior titles are excluded regardless of description availability."""
    assert search_jobs_msba_west.is_entry_to_mid_level("Senior Data Analyst", "") is False
    assert search_jobs_msba_west.is_entry_to_mid_level("Lead Business Analyst", "") is False
    assert search_jobs_msba_west.is_entry_to_mid_level("Director of Analytics", "") is False
    assert search_jobs_msba_west.is_entry_to_mid_level("Data Analyst", "") is True
