# Writing and Application Rules

## 1. Non-Negotiable Factual Guardrails
- **Employers, Dates, Titles, and Education**: NEVER change, extrapolate, or invent employers, job titles, employment dates, degrees, universities, or GPAs.
- **Completed vs. Planned Work**: Every bullet must reflect ONLY completed, verified features from `01_master_career_history.yaml` and `02_project_evidence_bank.yaml`. Any feature tagged as `planned_not_completed` must NEVER be claimed as an accomplished achievement.
- **Metrics Integrity**: Use exact verified metrics from the evidence bank (e.g., *~40% variance reduction with CUPED*, *51 automated tests*, *500+ SKUs*, *1,400+ extension users*, *400,000+ clinical trial studies*). NEVER fabricate business dollar impact or revenue percentages not verified in the evidence bank.

## 2. Dynamic Project Selection Rubric
For every job posting evaluated:
1. Scan the 22 projects in `02_project_evidence_bank.yaml`.
2. Score projects against the job description's primary domain, technical stack, and day-to-day problems.
3. Select the **top 3–4 most compelling projects**.
4. Justify the selection in `01_fit_assessment.md` and `04_changelog_and_audit.md`.
5. Organize the resume so that the selected projects and the relevant work experience framing (`GM Investing` and `UCLA Neurology`) address the job's highest-priority requirements directly.

## 3. Bullet Formatting & ATS Precision (XYZ Formula)
- Every bullet must follow the Google XYZ formula: **Accomplished [X], as measured by [Y], by doing [Z]**.
- **ATS Exact-Phrase Alignment**: When your verified work used a tool or technique that the JD describes with an exact industry noun phrase (e.g., the JD asks for *"dimensional modeling"* and your project built *"dbt star-schema marts"*), use the exact JD noun phrase (`dimensional modeling`) while preserving the factual technical mechanism.
- Avoid buzzwords, fluff, and subjective adjectives (*"passionate"*, *"results-driven"*, *"rockstar"*). Let the architectural mechanisms and verified metrics demonstrate competence.

## 4. Document Length & Formatting Standards
- **Resume**: Must strictly fit on **1 single page** (target: 350–450 words total, tight margins, 10pt font in `.docx` and `.pdf`).
- **Cover Letter**: Must be **180–250 words** across 3–4 concise paragraphs:
  1. *Hook & Value Proposition*: Why this specific company/mission and the core problem you can solve on Day 1.
  2. *Two Verified Evidence Proof Points*: Deep-link 2 relevant projects with live URLs (`cti-dashboard.fly.dev`, `ticker-change.fly.dev`, GitHub repos) and concrete metrics.
  3. *Call to Action*: Clear, low-friction request to discuss how your background directly addresses their current initiatives.
- **No Raw LaTeX Math Delimiters**: Never emit raw LaTeX (`$...$`, `$$...$$`, `\text{...}`). Use native Unicode symbols (`≤`, `≥`, `∑`, `×`, `Δ`) and monospace code spans.
