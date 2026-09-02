# Task 1 Report: Environment & Dependency Setup

## Status: DONE

### Summary
1. Successfully installed `ats-scrapers[scrapers]` (v0.2.0) into the poetry virtual environment.
2. Verified that `ats_scrapers` and scrapers `GreenhouseScraper`, `LeverScraper`, and `AshbyScraper` can be cleanly imported.
3. Added `ats-scrapers = {version = ">=0.2.0", extras = ["scrapers"]}` under `[tool.poetry.dependencies]` in `pyproject.toml`.
4. Committed changes with git.

### Git Commit
- **Commit**: `5321cba0a5eec7cb46351cdc82805fafd70a6697`
- **Message**: `build: add ats-scrapers dependency`

### Verification Output
```text
$ poetry run python -c "import ats_scrapers; from ats_scrapers.scrapers import GreenhouseScraper, LeverScraper, AshbyScraper; print('ats-scrapers successfully imported')"
ats-scrapers successfully imported
```
