# Running JobSpy Custom on macOS

This repo currently runs on a Linux box via cron, every 2 days. These are
the steps to get it running on macOS instead, once a day.

## 1. Prerequisites

```bash
# Python 3.10+ (check first: python3 --version)
brew install python@3.12

# Poetry (dependency manager this project uses)
brew install poetry
```

## 2. Clone and install

```bash
git clone https://github.com/consciouspenguin/Jobspy-Custom.git
cd Jobspy-Custom
poetry install
```

## 3. Configure the email step

The pipeline emails a daily Excel report via Gmail SMTP. Neither
`credentials.json` nor `pipeline_config.json` are in git (both are
gitignored — the second one holds an SMTP password) so you need to
recreate `pipeline_config.json` locally:

```bash
cp pipeline_config.example.json pipeline_config.json
```

Then edit `pipeline_config.json` and fill in:

- `sender_email` — the Gmail address sending the report
- `sender_password` — a **Gmail App Password**, not the account
  password. Generate one at https://myaccount.google.com/apppasswords
  (requires 2-Step Verification to be enabled on that Google account).
- `receiver_email` — where the report should land

`credentials.json` (a Google service-account key) exists in the old
Linux copy but isn't referenced by any script currently in this repo —
you likely don't need to recreate it. If a run errors looking for it,
check `jobspy/` for where it's expected and copy it over from the old
machine.

## 4. Test it manually before scheduling anything

```bash
poetry run python run_pipeline.py
```

This runs 4 scrapers in parallel (`jobspy/search_jobs_*.py`), compiles
their CSVs into an Excel workbook under
`jobspy/scraped jobs/results/YYYY_MM/`, and emails it. Confirm the
email arrives before moving on — scheduling a broken config just means
silent daily failures.

## 5. Schedule it to run daily

macOS has `cron`, but it's not the native fit — cron jobs need a manual
Full Disk Access grant in System Settings, and cron simply skips a run
if the Mac was asleep at the scheduled time. **launchd** is what macOS
actually uses for this, runs as your user, and reschedules a missed run
for when the Mac wakes up.

A ready-to-edit job is in [`launchd/com.jobspy-custom.pipeline.plist`](launchd/com.jobspy-custom.pipeline.plist).

```bash
# 1. Edit the plist: replace REPLACE_WITH_REPO_PATH and
#    REPLACE_WITH_HOME_DIR with your actual paths, e.g.:
sed -i '' "s|REPLACE_WITH_REPO_PATH|$(pwd)|g; s|REPLACE_WITH_HOME_DIR|$HOME|g" \
  launchd/com.jobspy-custom.pipeline.plist

# 2. Install it
cp launchd/com.jobspy-custom.pipeline.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobspy-custom.pipeline.plist

# 3. Test it fires correctly right now, without waiting for 6am
launchctl kickstart -k gui/$(id -u)/com.jobspy-custom.pipeline
tail -f pipeline_cron.log
```

The default schedule is 6:00 AM local time, daily. To change the time,
edit the `Hour`/`Minute` values in the plist before installing (and if
you already installed it, `launchctl bootout` first, then re-bootstrap
after editing).

Notes:
- This is a **LaunchAgent** (not a LaunchDaemon), so it only runs while
  you're logged in — that's normal for a per-user script like this and
  matches how it ran on the Linux box.
- `poetry` needs to be resolvable from the plist's `PATH`. The template
  covers Homebrew's Apple Silicon (`/opt/homebrew/bin`) and Intel
  (`/usr/local/bin`) locations plus `pipx`'s (`~/.local/bin`). Run
  `which poetry` and add its directory to the plist's `PATH` if it's
  installed somewhere else.

## 6. Verify

```bash
launchctl list | grep jobspy-custom
```

Should show the job with a `0` last-exit-status after it's run once.
Logs land in `pipeline_cron.log` in the repo root (gitignored).
