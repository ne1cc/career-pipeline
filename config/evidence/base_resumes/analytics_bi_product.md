# Alex Sun
Seattle, WA | (206) 822-2070 | suna16@uw.edu | [github.com/ne1cc](https://github.com/ne1cc) | [linkedin.com/in/alexsun-analytics](https://www.linkedin.com/in/alexsun-analytics)

---

## Education
**University of Washington, Michael G. Foster School of Business** | Seattle, WA  
*Master of Science in Business Analytics (MSBA)* | Expected June 2027  
- Coursework: Advanced SQL & Data Warehousing, Business Decision Modeling, Applied Machine Learning, Econometrics

**Western Governors University** | Salt Lake City, UT  
*Bachelor of Science in Computer Science* | June 2024  
- Coursework: Data Structures & Algorithms, Software Engineering, Relational Database Management, Discrete Math

**University of California, Los Angeles (UCLA)** | Los Angeles, CA  
*Bachelor of Science in Psychobiology* | March 2020 | GPA: 3.43  
- Coursework: Statistical Methods for Life Sciences, Experimental Design & Statistical QC, Molecular Systems

---

## Technical Skills
- **Languages & Analysis:** SQL (Window Functions, CTEs, Performance Tuning), Python (Pandas, Polars, NumPy, SciPy), R
- **Analytics Engineering & BI:** dbt (Core, Star Schema, Dimensional Modeling), Dagster, Tableau, Streamlit, Excel (Advanced Modeling)
- **Databases & Warehousing:** DuckDB, Delta Lake, PostgreSQL, SQLite, Snowflake / BigQuery (Familiarity)
- **Experimentation & ML:** A/B Testing, CUPED Variance Reduction, Sample Ratio Mismatch (SRM), LightGBM, Scikit-Learn

---

## Analytics & Product Projects

**Product A/B Testing & Experimentation Engine** | [github.com/ne1cc/ab-testing-dagster](https://github.com/ne1cc/ab-testing-dagster)  
*Stack: Dagster, dbt, DuckDB, Python, SciPy, Statsmodels*
- Built end-to-end product experimentation pipeline automating daily partition ingestion, telemetry transformations, and metric calculations.
- Implemented CUPED variance reduction using pre-experiment covariates, reducing experiment duration and sample size needs by ~40%.
- Engineered automated Sample Ratio Mismatch (SRM) guardrails via chi-square goodness-of-fit to halt metric computation if traffic skews.
- Modeled conversion rates, ARPU, and 7-day retention cohorts across treatment and control arms in analytics-ready dbt marts.

**SaaS Customer Lifecycle & CLV Control Center** | [github.com/ne1cc/customer-churn-clv-engine](https://github.com/ne1cc/customer-churn-clv-engine)  
*Stack: DuckDB, LightGBM, BG/NBD, Gamma-Gamma, Streamlit, Python*
- Extracted customer engagement feature marts using DuckDB window functions (recency, frequency, monetary value, tenure velocity).
- Trained LightGBM 90-day churn classifier and calibrated cost-weighted intervention thresholds to optimize retention campaign ROI.
- Implemented probabilistic BG/NBD and Gamma-Gamma models to forecast 12-month expected customer lifetime value across account tiers.
- Built interactive Streamlit dashboard allowing product and growth teams to simulate campaign returns across risk segments.

**Multi-Domain E-Commerce & Customer SLA Lakehouse** | [ne1cc.github.io/lakehouse-engine](https://ne1cc.github.io/lakehouse-engine/dashboard.html)  
*Stack: DuckDB, dbt, Polars, Python, GitHub Pages*
- Architected dimensional star-schema marts modeling 99,441 e-commerce orders, customer purchase behavior, and transit times.
- Designed rolling cohort retention and carrier fulfillment latency marts, identifying delivery SLA bottlenecks across regional shipping routes.

---

## Professional Experience

**GM Investing** | Seattle, WA  
*Assistant Project Manager, Systems & Execution* | September 2019 – May 2020
- Built and maintained centralized materials procurement and inventory tracking engine monitoring 500+ SKUs across 15+ subcontractors.
- Standardized subcontractor scheduling and milestone tracking workflows, cutting delivery delays and schedule variance.
- Reconciled vendor invoices against purchase orders and project budgets, preventing cost overruns across concurrent site renovations.
- Prepared weekly operational KPI briefings for principal stakeholders, synthesizing milestone burn rates and resource dependencies.

**UCLA Department of Neurology** | Los Angeles, CA  
*Undergraduate Researcher / Data Analyst* | September 2017 – June 2020
- Extracted and modeled kinetic time-series aggregation curves from spectroscopy trials studying amyloid-beta protein dynamics.
- Automated data extraction and multi-run curve-fitting routines using Python and R, reducing analysis turnaround from hours to minutes.
- Established standardized data cleaning, baseline normalization, and statistical quality control protocols across hundreds of experimental runs.
- Mentored and trained 3 junior undergraduate researchers on experimental protocols and statistical data integrity standards.
