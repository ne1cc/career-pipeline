# Alex Sun
Seattle, WA | (206) 822-2070 | suna16@uw.edu | [github.com/ne1cc](https://github.com/ne1cc) | [linkedin.com/in/alexsun-analytics](https://www.linkedin.com/in/alexsun-analytics)

---

## Education
**University of Washington, Michael G. Foster School of Business** | Seattle, WA  
*Master of Science in Business Analytics (MSBA)* | Expected June 2027  
- Coursework: Advanced Database Systems, Applied Machine Learning, Econometrics, Decision Modeling

**Western Governors University** | Salt Lake City, UT  
*Bachelor of Science in Computer Science* | June 2024  
- Coursework: Data Structures & Algorithms, Software Architecture, Database Management

**University of California, Los Angeles (UCLA)** | Los Angeles, CA  
*Bachelor of Science in Psychobiology* | March 2020 | GPA: 3.43  
- Coursework: Statistical Methods for Life Sciences, Biochemistry, Physiological Systems, Experimental Design & Statistical QC

---

## Technical Skills
- **Clinical Analytics & Life Sciences:** Clinical Trial Protocol Analysis, Site Congestion Modeling, EHR Cohort Extraction, SDOH
- **Languages & Analysis:** Python (Pandas, Polars, SciPy, Scikit-Learn), SQL, R, Bash
- **Data Engineering & BI:** DuckDB, dbt (Core, Star Schema), Parquet, Streamlit, Tableau, Docker
- **Biostatistics & ML:** Taylor-Series Survey Weighting, Kinetic Modeling, XGBoost/LightGBM, SHAP Explainability

---

## Healthcare & Clinical Data Projects

**Clinical Trial Feasibility & Site Congestion Intelligence** | [cti-dashboard.fly.dev](https://cti-dashboard.fly.dev)  
*Stack: DuckDB, dbt, Python, Streamlit, Parquet, Docker, Fly.io*
- Architected Medallion data pipeline ingesting ClinicalTrials.gov API v2 data across 400,000+ protocol records.
- Built dbt star-schema marts modeling investigator density, historical trial phase durations, and geographic site saturation.
- Deployed interactive Streamlit feasibility dashboard on Fly.io allowing clinical ops teams to evaluate site competition.
- Enabled sub-second analytical window queries over millions of clinical protocol attributes using columnar DuckDB storage.

**Heart Failure 30-Day Readmission Risk Predictor with SDOH** | [github.com/ne1cc/Heart-Failure-Decompensation-Predictor-with-SDOH](https://github.com/ne1cc/Heart-Failure-Decompensation-Predictor-with-SDOH)  
*Stack: Python, XGBoost, LightGBM, SHAP, Scikit-Learn, SQL*
- Extracted longitudinal EHR cohort filtering for patients 65+ with congestive heart failure and acute decompensation events.
- Fused clinical vitals (7-day weight gain, ejection fraction) with Area Deprivation Index (ADI) neighborhood social vulnerability metrics.
- Trained gradient boosted risk classifier with SHAP tree explainability to provide clinicians with individualized patient risk factor breakdowns.

**Survey-Design-Aware Childhood Stunting Pipeline** | [github.com/ne1cc/dhs-stunting-pipeline](https://github.com/ne1cc/dhs-stunting-pipeline)  
*Stack: Python 3.12, NumPy, SciPy, Pandas, Pytest*
- Built epidemiological pipeline computing childhood stunting prevalence from Demographic and Health Surveys (DHS) microdata.
- Modeled multi-stage stratified cluster sampling weights and Taylor-series linearized standard errors, correcting naive sample bias.

---

## Professional Experience

**UCLA Department of Neurology** | Los Angeles, CA  
*Undergraduate Researcher / Data Analyst* | September 2017 – June 2020
- Analyzed continuous kinetic curves and time-series aggregation data from spectroscopy trials studying amyloid-beta protein dynamics.
- Automated spectroscopy data extraction and multi-run curve-fitting routines using Python and R, reducing analysis turnaround from hours to minutes.
- Established standardized data cleaning, baseline normalization, and statistical quality control protocols across hundreds of experimental runs.
- Trained and mentored 3 junior undergraduate researchers on wet-lab experimental protocols and statistical data integrity standards.

**GM Investing** | Seattle, WA  
*Assistant Project Manager, Systems & Execution* | September 2019 – May 2020
- Built and maintained centralized materials procurement and inventory tracking engine monitoring 500+ SKUs across 15+ subcontractors.
- Standardized subcontractor scheduling and milestone tracking workflows, cutting delivery delays and schedule variance.
- Reconciled vendor invoices against purchase orders and project budgets, preventing cost overruns across concurrent site renovations.
- Prepared weekly operational KPI briefings for principal stakeholders, synthesizing milestone burn rates and resource dependencies.
