from pathlib import Path
import run_parallel
import run_pipeline

def test_parallel_runner_scripts_contain_msba():
    base_dir = Path(run_parallel.__file__).parent.resolve()
    jobspy_dir = base_dir / "jobspy"
    expected_scripts = [
        jobspy_dir / "search_jobs_remote.py",
        jobspy_dir / "search_jobs_seattle.py",
        jobspy_dir / "search_jobs_seattle_entry_level.py",
        jobspy_dir / "search_jobs_west.py",
        jobspy_dir / "search_jobs_seattle_msba.py",
        jobspy_dir / "search_jobs_msba_west.py",
        jobspy_dir / "search_jobs_ats.py",
        jobspy_dir / "search_jobs_portals.py",
    ]
    # We inspect the expected_outputs keys or scripts defined in run_parallel
    for script in expected_scripts:
        assert script.exists(), f"Script missing: {script}"
    assert "search_jobs_seattle_msba.py" in run_parallel.EXPECTED_OUTPUTS
    assert "search_jobs_msba_west.py" in run_parallel.EXPECTED_OUTPUTS
    assert len(run_parallel.EXPECTED_OUTPUTS) == 8

def test_pipeline_expected_outputs_mapping():
    outputs = run_pipeline.EXPECTED_OUTPUTS
    assert "seattle_msba_jobs.csv" in outputs
    assert outputs["seattle_msba_jobs.csv"] == "Seattle MSBA Jobs"
    assert "west_coast_msba_jobs.csv" in outputs
    assert outputs["west_coast_msba_jobs.csv"] == "West Coast MSBA Jobs"
    assert len(outputs) == 8
