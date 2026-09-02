5321cba build: add ats-scrapers dependency
---
 pyproject.toml | 1 +
 1 file changed, 1 insertion(+)
---
diff --git a/pyproject.toml b/pyproject.toml
index a1ae6a1..b4a6296 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -22,15 +22,16 @@ requests = "^2.31.0"
 beautifulsoup4 = "^4.12.2"
 pandas = "^2.1.0"
 numpy = ">=1.26.0"
 pydantic = "^2.3.0"
 tls-client = "^1.0.1"
 markdownify = "^1.1.0"
 regex = "^2024.4.28"
 gspread = "^6.2.1"
 openpyxl = "^3.1.5"
 google-api-python-client = "^2.198.0"
+ats-scrapers = {version = ">=0.2.0", extras = ["scrapers"]}
 
 [tool.poetry.group.dev.dependencies]
 jupyter = "^1.0.0"
 black = "*"
 pre-commit = "*"
