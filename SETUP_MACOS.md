# Running JobSpy Custom on macOS

This is the full setup guide for moving the daily job-scrape pipeline from
the Linux box (cron, every 2 days) onto a Mac (launchd, every day at 6:00 AM).

On Linux it currently looks like:

```
0 6 */2 * * cd ~/Github/Jobspy-Custom && poetry run python run_pipeline.py >> pipeline_cron.log 2>&1
```

On macOS, **do not use crontab**. Cron needs a manual Full Disk Access grant
and silently skips a run if the Mac was asleep. **launchd** is the native
scheduler: it runs as your user and catches up a missed 6 AM run when the
Mac wakes.

---

## 0. What you are installing

`poetry run python run_pipeline.py` does three things:

1. **Scrape** — `run_parallel.py` launches these four scripts at once:
   - `jobspy/search_jobs_remote.py` → `all_city_remote_jobs.csv`
   - `jobspy/search_jobs_seattle.py` → `seattle_jobs.csv`
   - `jobspy/search_jobs_seattle_entry_level.py` → `seattle_entry_level_jobs.csv`
   - `jobspy/search_jobs_west.py` → `west_coast_jobs.csv`
2. **Compile** — those CSVs become `jobspy/scraped jobs/results/YYYY_MM/Job_Report_YYYY_MM_DD.xlsx`
   - Drops `description`, `company_description`, and `job_url_direct`
   - Turns every `http(s)` cell into a clickable hyperlink
   - Auto-sizes columns (capped at width 80)
   - Adds a `(Filtered)` tab per segment for titles matching analyst / analytics / data
3. **Email** — attaches that workbook via Gmail SMTP using `pipeline_config.json`

Not part of the daily job (run them by hand if you want them):

- `jobspy/search_jobs_seattle_msba.py`
- `jobspy/search_jobs_msba_west.py`

A full run takes a while (four scrapers hitting several job boards). Test
the email config with a real run before scheduling anything.

---

## 1. Prerequisites

Install Homebrew if it is not already there:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

On Apple Silicon, add Homebrew to your PATH as the installer prints
(typically `eval "$(/opt/homebrew/bin/brew shellenv)"` in `~/.zprofile`).

Then:

```bash
# Confirm: python3 --version  →  3.10 or newer
brew install python@3.12

# Dependency manager this project uses
brew install poetry
```

Confirm both resolve:

```bash
which python3
which poetry
poetry --version
```

If `poetry` is missing after install, restart the terminal (or `eval "$(/opt/homebrew/bin/brew shellenv)"`) and check again.

---

## 2. Clone and install

Canonical repo: https://github.com/n1ecC/Jobspy-Custom.git
(the older `consciouspenguin/Jobspy-Custom` URL still redirects).

```bash
git clone https://github.com/n1ecC/Jobspy-Custom.git
cd Jobspy-Custom
poetry install
```

That creates a Poetry virtualenv and installs pandas, openpyxl, JobSpy
dependencies, etc. You do **not** need `jobspy/venv/` — that was a leftover
local venv on Linux.

---

## 3. Copy secrets from the Linux box (or recreate them)

These files are **gitignored** (never clone with them):

| File | Needed? | What it is |
|------|---------|------------|
| `pipeline_config.json` | **Yes** | Gmail SMTP user/app-password/recipient |
| `credentials.json` | No | Old Google service-account key; no script in this repo reads it |
| `proxies.txt` | Only if you use proxies | Optional; see [PROXIES.md](PROXIES.md) |
| `pipeline_cron.log` | No | Recreated on the first run |
| `jobspy/scraped jobs/` | No | Results and scraper logs; regenerated each run |

### Option A — copy the working config from Linux (preferred)

From the Mac, with SSH to the old machine:

```bash
scp USER@LINUX_HOST:~/Github/Jobspy-Custom/pipeline_config.json .
```

Replace `USER@LINUX_HOST` with the Linux login. Confirm the file landed in
the repo root and is **not** going to be committed (`git status` should not
list it as a new tracked file — it is gitignored).

### Option B — recreate from the template

```bash
cp pipeline_config.example.json pipeline_config.json
```

Then edit `pipeline_config.json`:

- `sender_email` — Gmail address that sends the report
- `sender_password` — a **Gmail App Password**, not the account password.
  Create one at https://myaccount.google.com/apppasswords
  (2-Step Verification must be on).
- `receiver_email` — where the report should land
- `smtp_server` / `smtp_port` — leave `smtp.gmail.com` / `587` unless you
  know you need something else

### Optional proxies

The four daily scrapers do not load a proxy file today. If Glassdoor/LinkedIn
start blocking you, see [PROXIES.md](PROXIES.md): copy `proxies.example.txt`
to `proxies.txt` (gitignored), fill in provider proxies, and wire them into
the search scripts via `scrape_jobs(..., proxies=...)`.

```bash
cp proxies.example.txt proxies.txt
# edit proxies.txt, then optionally:
poetry run python jobspy/proxy_utils.py proxies.txt
```

---

## 4. Test it manually before scheduling anything

```bash
cd /path/to/Jobspy-Custom
poetry run python run_pipeline.py
```

Watch for:

1. Four scrapers start (`[START] Spawning search_jobs_...`)
2. Excel created under `jobspy/scraped jobs/results/YYYY_MM/Job_Report_YYYY_MM_DD.xlsx`
3. `Email sent successfully!`

Open the `.xlsx` and confirm:

- No `job_url_direct` column
- `job_url` / `company_url` / other URL cells are clickable
- Columns are wide enough to read

If SMTP fails, it is almost always the app password or 2-Step Verification.
Do not install launchd until a manual run emails successfully — a broken
schedule just means silent daily failures.

Scraper logs (separate from the pipeline log):

```
jobspy/scraped jobs/logs/YYYY_MM/search_jobs_remote.log
jobspy/scraped jobs/logs/YYYY_MM/search_jobs_seattle.log
...
```

---

## 5. Schedule it with launchd (not cron)

A template job lives in
[`launchd/com.jobspy-custom.pipeline.plist`](launchd/com.jobspy-custom.pipeline.plist).

**Copy first, then fill in paths on the copy** — do not `sed` the file in
the git repo, or a later `git pull` will fight your local paths.

```bash
# From the repo root
cp launchd/com.jobspy-custom.pipeline.plist ~/Library/LaunchAgents/

sed -i '' "s|REPLACE_WITH_REPO_PATH|$(pwd)|g; s|REPLACE_WITH_HOME_DIR|$HOME|g" \
  ~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist

# Confirm no placeholders remain
grep REPLACE_ ~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist && echo "FIX PLACEHOLDERS" || echo "plist looks good"

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist

# Fire it now (don't wait until 6 AM)
launchctl kickstart -k gui/$(id -u)/com.jobspy-custom.pipeline
tail -f pipeline_cron.log
```

Default schedule: **6:00 AM local, daily**. Linux was every 2 days; this is
once a day. To change the time, edit `Hour` / `Minute` in
`~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist`, then:

```bash
launchctl bootout gui/$(id -u)/com.jobspy-custom.pipeline
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist
```

Notes:

- This is a **LaunchAgent** (not a LaunchDaemon). It runs while you are
  logged in, which matches how it ran on Linux. Stay logged in (lid closed
  with “prevent sleep” / power adapter is fine; a full logout will skip it).
- If the Mac is asleep at 6 AM, launchd runs the job when it next wakes.
- `poetry` must be on the plist's `PATH`. The template includes Homebrew
  Apple Silicon (`/opt/homebrew/bin`), Intel (`/usr/local/bin`), and pipx
  (`~/.local/bin`). If `which poetry` prints something else, add that
  directory to the `PATH` string in the plist.

---

## 6. Verify

```bash
launchctl list | grep jobspy-custom
```

After a successful run the last-exit-status column should be `0`.

Pipeline stdout/stderr:

```
tail -f /path/to/Jobspy-Custom/pipeline_cron.log
```

(`pipeline_cron.log` is gitignored.)

---

## 7. Turn off the Linux cron job

Once the Mac has emailed a report on its own, remove the Linux crontab
entry so you do not get two reports.

On the Linux box:

```bash
crontab -l
crontab -e
# delete the Jobspy-Custom line, save, quit
crontab -l   # confirm it's gone
```

Leave the Linux clone around until you are happy with a few Mac runs.

---

## 8. Updating the code later

```bash
cd /path/to/Jobspy-Custom
git pull
poetry install
```

`pipeline_config.json` is gitignored and will not be overwritten.

If `launchd/com.jobspy-custom.pipeline.plist` **changed** in git, copy the
new template to `~/Library/LaunchAgents/`, re-run the `sed` path
replacement, then `bootout` + `bootstrap` as in section 5. Do not copy a
fresh template over the installed plist without replacing
`REPLACE_WITH_*` again.

---

## 9. Uninstall

```bash
launchctl bootout gui/$(id -u)/com.jobspy-custom.pipeline
rm ~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist
```

That only removes the schedule. The repo, Poetry env, and
`pipeline_config.json` stay until you delete them yourself.

---

## 10. Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `poetry: command not found` from launchd | `which poetry` — add that directory to the plist's `PATH` |
| `ERROR: Sender password is not configured` | `pipeline_config.json` missing or still has `ENTER_*` placeholders |
| SMTP / `Authentication failed` | App password, 2-Step Verification, "less secure" is not the right setting — use an App Password |
| Job never fires | Still logged in? `launchctl list \| grep jobspy-custom`? Placeholders left in the plist? |
| Job ran at the wrong time | Mac timezone; `Hour`/`Minute` are local time |
| Empty / missing Excel tabs | Matching CSV missing under `jobspy/scraped jobs/results/YYYY_MM/` — read that scraper's log |
| Duplicate emails | Linux crontab still installed (section 7) |
| `REPLACE_WITH_REPO_PATH` in the log | `sed` was not applied to the **LaunchAgents** copy |

---

## File map

```
Jobspy-Custom/
├── SETUP_MACOS.md                          ← this file
├── run_pipeline.py                         ← scrape → Excel → email
├── run_parallel.py                         ← launches the 4 daily scrapers
├── pipeline_config.example.json            ← copy to pipeline_config.json
├── pipeline_config.json                    ← gitignored secrets
├── launchd/com.jobspy-custom.pipeline.plist
├── proxies.example.txt
├── jobspy/search_jobs_remote.py
├── jobspy/search_jobs_seattle.py
├── jobspy/search_jobs_seattle_entry_level.py
├── jobspy/search_jobs_west.py
└── jobspy/scraped jobs/                    ← gitignored results + logs
```
