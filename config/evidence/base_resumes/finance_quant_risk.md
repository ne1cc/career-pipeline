# Alex Sun
Seattle, WA | (206) 822-2070 | suna16@uw.edu | [github.com/ne1cc](https://github.com/ne1cc) | [linkedin.com/in/alexsun-analytics](https://www.linkedin.com/in/alexsun-analytics)

---

## Education
**University of Washington, Michael G. Foster School of Business** | Seattle, WA  
*Master of Science in Business Analytics (MSBA)* | Expected June 2027  
- Coursework: Financial Econometrics, Business Decision Modeling, Applied Machine Learning, Advanced SQL

**Western Governors University** | Salt Lake City, UT  
*Bachelor of Science in Computer Science* | June 2024  
- Coursework: Algorithms & Optimization, Database Systems, Computer Architecture, Software Engineering

**University of California, Los Angeles (UCLA)** | Los Angeles, CA  
*Bachelor of Science in Psychobiology* | March 2020 | GPA: 3.43  
- Coursework: Statistical Inference, Quantitative Methodologies, Molecular Systems

---

## Technical Skills
- **Quantitative & Financial Analytics:** Value at Risk (Parametric & Historical VaR), Max Drawdown, Beta, Volatility Modeling, Backtesting
- **Languages & Frameworks:** Python (NumPy, SciPy, Pandas, Flask, FastAPI), SQL, R, Bash
- **Data & Market Feeds:** SEC EDGAR, FINRA OTC, yfinance, Finnhub, FMP, SQLite, Redis, MongoDB
- **Tools & DevOps:** Docker, Fly.io, Git, Prometheus, Grafana, Linux Terminal

---

## Financial & Quantitative Projects

**ticker/change — Equity Research & Risk Terminal** | [ticker-change.fly.dev](https://ticker-change.fly.dev)  
*Stack: Python, Flask, SQLite, Docker, Fly.io, SEC EDGAR, Finnhub*
- Built production equity research terminal providing pre-trade risk analysis across fundamentals, sentiment, and risk metrics.
- Calculated historical volatility, max drawdown, market beta, and parametric vs historical 95%/99% Value at Risk (VaR).
- Aggregated options put/call open interest and insider trading filings from SEC EDGAR into executive risk summaries.
- Deployed on Fly.io using a two-tier SQLite TTL cache ensuring sub-100ms response times while throttling third-party rate limits.

**Point-in-Time Security Master & Survivorship Bias Engine** | [github.com/ne1cc/ticker-change-intelligence](https://github.com/ne1cc/ticker-change-intelligence)  
*Stack: Python, SQL, SEC EDGAR, FINRA OTC, Pytest, GitHub Actions*
- Constructed point-in-time security master linking transient ticker symbols to permanent SEC Central Index Keys (CIK).
- Ingested historical SEC and FINRA corporate action filings to model ticker rebrands, bankruptcies, and symbol recycling.
- Built temporal query API returning exact active company and symbol for any arbitrary historical date t, eliminating survivorship bias.

**Async Financial Ingestion & Indicator Microservice** | [github.com/ne1cc/financial-pipeline](https://github.com/ne1cc/financial-pipeline)  
*Stack: Python 3.11, FastAPI, MongoDB (Motor), Redis, Docker Compose, Prometheus*
- Developed non-blocking async market data ingestion service using FastAPI, motor, and redis.asyncio.
- Implemented pure Pandas technical indicator calculations (SMA, RSI, MACD) and market regime classification logic.
- Configured Prometheus metric scrapers and Grafana dashboards tracking sub-millisecond cache hits and API ingestion latencies.

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
