# Alex Sun
Seattle, WA | (206) 822-2070 | suna16@uw.edu | [github.com/ne1cc](https://github.com/ne1cc) | [linkedin.com/in/alexsun-analytics](https://www.linkedin.com/in/alexsun-analytics)

---

## Education
**University of Washington, Michael G. Foster School of Business** | Seattle, WA  
*Master of Science in Business Analytics (MSBA)* | Expected June 2027  
- Coursework: Advanced Database Systems, Data Modeling, Cloud Analytics Architecture, Statistical Inference

**Western Governors University** | Salt Lake City, UT  
*Bachelor of Science in Computer Science* | June 2024  
- Coursework: Data Structures & Algorithms, Operating Systems, Relational Database Management, Software Engineering

**University of California, Los Angeles (UCLA)** | Los Angeles, CA  
*Bachelor of Science in Psychobiology* | March 2020 | GPA: 3.43  
- Coursework: Statistical Methods for Life Sciences, Research Design & Data Integrity

---

## Technical Skills
- **Data Engineering & Lakehouse:** DuckDB, Delta Lake, Polars, Parquet (Hive Partitioning), dbt (Core / Dimensional Modeling), Dagster
- **Languages:** Python, SQL, Bash, Rust, TypeScript
- **Orchestration & DevOps:** Docker / Docker Compose, Apache Airflow, GitHub Actions CI/CD, Ofelia Scheduler
- **Databases & Storage:** PostgreSQL, SQLite, MongoDB (Motor), Redis, S3/Cloud Storage, Uber H3 Spatial Indexing

---

## Data Engineering Projects

**Contract-First Local Lakehouse Engine** | [github.com/ne1cc/local-lakehouse-engine](https://github.com/ne1cc/local-lakehouse-engine)  
*Stack: DuckDB, dbt, Polars, Delta Lake, Uber H3, Python, GitHub Actions*
- Architected streaming and batch lakehouse ingesting NYC Citi Bike GBFS, Open-Meteo Weather, and Air Quality APIs into Medallion tables.
- Enforced strict JSON schema contracts with Polars dead-letter quarantine, rejecting corrupt payloads before landing in Bronze.
- Engineered SCD Type 2 tracking for station capacity changes and Uber H3 hexagonal spatial indexing for mobility joins.
- Built gradient-boosted ML classifier predicting bike dock starvation 2 hours in advance; verified by 51 automated CI tests.

**Multi-Domain E-Commerce & SLA Lakehouse** | [ne1cc.github.io/lakehouse-engine](https://ne1cc.github.io/lakehouse-engine/dashboard.html)  
*Stack: DuckDB, dbt, Polars, Python, SQL, GitHub Pages*
- Designed star-schema dimensional models with fact orders, order items, customer cohorts, and carrier delivery performance across 99.4k orders.
- Constructed rolling cohort retention and fulfillment SLA latency marts identifying regional delivery bottlenecks.
- Implemented deterministic MD5 surrogate key hashing and incremental dbt models for high-throughput transformation runs.

**Containerized DuckDB/Parquet OLAP Sidecar Pipeline** | [github.com/ne1cc/duckdb-olap-pipeline](https://github.com/ne1cc/duckdb-olap-pipeline)  
*Stack: DuckDB, Parquet, Ofelia, Docker, Python 3.11, ZSTD*
- Designed sidecar container architecture decoupling lightweight Ofelia cron scheduler from ephemeral DuckDB compute workers.
- Built Bronze-Silver pipeline applying ZSTD Hive-partitioning, explicit schema casting, deduplication, and automated partition reconciliation.
- Achieved 10x storage compression and zero-cloud licensing overhead using embedded local columnar processing.

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
