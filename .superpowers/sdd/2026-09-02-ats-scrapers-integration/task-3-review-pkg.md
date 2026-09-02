b133dc1 feat(enrichment): add ATS apply type classification
---
 pipeline_enrichment.py | 15 +++++++++++++++
 1 file changed, 15 insertions(+)
---
diff --git a/pipeline_enrichment.py b/pipeline_enrichment.py
index e666cc1..cbf202f 100644
--- a/pipeline_enrichment.py
+++ b/pipeline_enrichment.py
@@ -55,20 +55,35 @@ def parse_posted_date(value) -> date | None:
             return datetime.strptime(text[:19], fmt).date()
         except ValueError:
             continue
     return None
 
 
 def classify_apply_type(row: pd.Series) -> str:
     site = str(row.get("site", "") or "").lower()
     job_url = str(row.get("job_url", "") or "").lower()
     job_url_direct = str(row.get("job_url_direct", "") or "").strip().lower()
+    combined_urls = f"{job_url} {job_url_direct}"
+
+    if "greenhouse.io" in combined_urls:
+        return "Greenhouse Direct"
+    if "lever.co" in combined_urls:
+        return "Lever Direct"
+    if "ashbyhq.com" in combined_urls:
+        return "Ashby Direct"
+    if "myworkdayjobs.com" in combined_urls or "workday" in combined_urls:
+        return "Workday Direct"
+    if "smartrecruiters.com" in combined_urls:
+        return "SmartRecruiters Direct"
+
+    if site == "ats":
+        return "Company ATS Direct"
 
     if site == "indeed":
         if "indeed-apply" in job_url_direct or "indeed-apply" in job_url:
             return "Indeed Easy Apply"
         if job_url_direct and job_url_direct not in ("", "nan", "none"):
             return "Company Site"
         return "Indeed (unknown apply)"
 
     if site == "linkedin":
         if not job_url_direct or job_url_direct in ("nan", "none"):
