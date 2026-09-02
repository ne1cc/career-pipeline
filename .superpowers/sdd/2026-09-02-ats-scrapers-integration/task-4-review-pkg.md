ae9737d feat(runner): add search_jobs_ats.py to parallel runner
---
 run_parallel.py | 2 ++
 1 file changed, 2 insertions(+)
---
diff --git a/run_parallel.py b/run_parallel.py
index f432988..535f5c4 100644
--- a/run_parallel.py
+++ b/run_parallel.py
@@ -10,20 +10,21 @@ import subprocess
 def main():
     # 1. Define paths
     base_dir = Path(__file__).parent.resolve()
     jobspy_dir = base_dir / "jobspy"
     
     scripts = [
         jobspy_dir / "search_jobs_remote.py",
         jobspy_dir / "search_jobs_seattle.py",
         jobspy_dir / "search_jobs_seattle_entry_level.py",
         jobspy_dir / "search_jobs_west.py",
+        jobspy_dir / "search_jobs_ats.py",
     ]
     
     # Verify all scripts exist
     for script in scripts:
         if not script.exists():
             print(f"Error: Script not found: {script}")
             sys.exit(1)
             
     # 2. Setup logs directory
     timestamp_month = datetime.now().strftime("%Y_%m")
@@ -142,20 +143,21 @@ def main():
     print("All parallel scrape tasks have completed.")
     print("=" * 80)
     
     # 6. Show summary of generated results files
     print("\nDiscrete Results Summary:")
     expected_outputs = {
         "search_jobs_remote.py": "all_city_remote_jobs.csv",
         "search_jobs_seattle.py": "seattle_jobs.csv",
         "search_jobs_seattle_entry_level.py": "seattle_entry_level_jobs.csv",
         "search_jobs_west.py": "west_coast_jobs.csv",
+        "search_jobs_ats.py": "ats_direct_jobs.csv",
     }
     
     for script_name, csv_name in expected_outputs.items():
         csv_path = results_dir / csv_name
         if csv_path.exists():
             size_mb = csv_path.stat().st_size / (1024 * 1024)
             print(f" - {csv_name}: SAVED ({size_mb:.2f} MB)")
             print(f"   Path: {csv_path}")
         else:
             print(f" - {csv_name}: NOT FOUND (Likely failed or no jobs found)")
