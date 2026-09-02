# Task 1 Brief: Environment & Dependency Setup

## Objective
Install `ats-scrapers[scrapers]` into the project's Poetry virtual environment at `/Users/alex/Github/n1ecC/Jobspy-Custom`, update `pyproject.toml`, and verify that `ats_scrapers` can be imported cleanly.

## Steps
1. Run `poetry run pip install "ats-scrapers[scrapers]"` in `/Users/alex/Github/n1ecC/Jobspy-Custom`.
2. Run verification command:
   `poetry run python -c "import ats_scrapers; from ats_scrapers.scrapers import GreenhouseScraper, LeverScraper, AshbyScraper; print('ats-scrapers successfully imported')"`
   Ensure it outputs: `ats-scrapers successfully imported`.
3. In `pyproject.toml`, add `ats-scrapers = {version = ">=0.2.0", extras = ["scrapers"]}` under `[tool.poetry.dependencies]`.
4. Run `git add pyproject.toml && git commit -m "build: add ats-scrapers dependency"`.

## Report File
Write your completion report to `/Users/alex/Github/n1ecC/Jobspy-Custom/.superpowers/sdd/2026-09-02-ats-scrapers-integration/task-1-report.md`.
Report status: DONE or BLOCKED.
Include git commit hash and test output.
