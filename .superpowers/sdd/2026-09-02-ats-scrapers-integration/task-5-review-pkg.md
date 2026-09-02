eff2f81 feat(pipeline): include ATS Direct Jobs in daily Excel workbook
---
 run_pipeline.py | 1 +
 1 file changed, 1 insertion(+)
---
diff --git a/run_pipeline.py b/run_pipeline.py
index 826278b..4edcc78 100755
--- a/run_pipeline.py
+++ b/run_pipeline.py
@@ -285,20 +285,21 @@ def send_email_with_attachment(config, excel_path, stats, has_previous_run):
 def main():
     config = load_config()
     validate_config(config)
     
     # List of expected CSV outputs mapping to target Excel tabs
     expected_outputs = {
         "all_city_remote_jobs.csv": "Remote Jobs",
         "seattle_jobs.csv": "Seattle Jobs",
         "seattle_entry_level_jobs.csv": "Seattle Entry Level",
         "west_coast_jobs.csv": "West Coast Jobs",
+        "ats_direct_jobs.csv": "ATS Direct Jobs",
     }
     
     # Locate scraped jobs results directory
     timestamp_month = datetime.now().strftime("%Y_%m")
     results_dir = BASE_DIR / "jobspy" / "scraped jobs" / "results" / timestamp_month
     
     # Check if results folder exists or create it
     results_dir.mkdir(parents=True, exist_ok=True)
     
     run_scrapers()
