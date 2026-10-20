# JobCockpit: setup and daily use

## One-time setup (about 10 minutes)

### 1. Reload your shell and tmux

```zsh
source ~/.zshrc
tmux source-file ~/.tmux.conf
```

You now have the `cockpit` command and `Alt+Shift+j` (no prefix) to pop open the TUI.

### 2. Drop in your resumes

Use these exact names in `~/resumes/`:

```
swe_backend_v1.pdf
data_eng_lakehouse_v1.pdf
analytics_msba_v1.pdf
```

To rename or relocate them, edit `[resumes]` in `config/scoring_rules.toml`. Missing files fall back to `Jobspy-Custom/resumes/`.

### 3. Edit the outreach template

Open `config/scoring_rules.toml` and fix `[outreach] template`. It currently says "I'm Alex" and "I just applied". Keep it true to how you actually use it (see `y` below).

### 4. Gmail status tracking (optional but worth it)

1. Turn on 2-step verification, then create an App Password at `myaccount.google.com/apppasswords`.
2. Add `export GMAIL_USER=your@gmail.com` to `~/.zshenv`.
3. Run `cockpit keychain-setup --run` and paste the 16-character password when prompted.
4. Test it with `cockpit sync-mail`.

### 5. Twice-daily auto sync (optional)

```zsh
cockpit install-agent
```

This runs `ingest` and `sync-mail` at 08:30 and 13:30. Logs go to `/tmp/jobcockpit.{out,err}.log`.

> **Important:** the agent does **not** scrape. It only imports CSVs your existing scrapers already produced. Scraping still happens through `run_pipeline.py` (or however you run it now). Schedule that to run before 08:30 if you want fresh jobs.

### 6. Status bar (optional)

Add this to your tmux `status-right`:

```tmux
#(cat /tmp/jobcockpit_tmux.cache)
```

It shows `Jobs: 6/15 | Q:34 | H:2`.

## Your daily recruiting loop

### Morning: refresh the queue

```zsh
cockpit ingest
```

Reads this month's `*_jobs.csv`, dedupes across boards, scores, and queues. Re-running is safe: jobs you applied to or skipped are never overwritten.

### Afternoon: apply

Press `Alt+Shift+j` anywhere to open the popup, which lists jobs by score, best first.

| Key | Does |
|---|---|
| `j` / `k` | Move down / up |
| `o` or Enter | Copy the resume path **and** open the application page |
| `O` | Same, but opens in a background tab so you can stage several |
| `p` | QuickLook preview of the staged resume |
| `1` / `2` / `3` | Switch resume (swe / data / analytics) and re-copy the path |
| `a` | Mark **Applied** and jump to the next job |
| `s` | Skip |
| `h` | Hold for a referral (type the contact, then Enter) |
| `y` | Copy the outreach message for this job |
| `f` | Cycle the view: Queued, Referral Hold, Applied, All |
| `R` | Sync Gmail now |
| `q` | Quit |

### The per-application rhythm

1. Press `o`. The page opens and the resume path is on your clipboard.
2. Let Simplify autofill the text fields.
3. At the file upload, press `Cmd+Shift+G`, `Cmd+V`, then Enter twice.
4. Submit, return to the TUI, and press `a`.

### Tips

- Check the detail pane. If the archetype looks wrong (a data role scored as SWE), press `1`, `2` or `3` *before* uploading.
- If a job is worth a referral, press `h` and write the contact. Holds older than 24 hours turn into `REFERRAL_STALE`. Check them with `f`.
- Use `y` **after** you apply, to message a recruiter or an engineer. The message is generic, so add a personal line before sending.
- **No TUI?** `cockpit sprint --limit 15` runs the same flow as a plain prompt loop (`a` / `s` / `h` / `q`).

## Track what's working

```zsh
cockpit stats
```

Shows, by resume archetype and by job board: applied, OA/phone signals, and median hours to apply. After a week, use it to decide which resume and which boards are worth your time.

Mail sync moves jobs to `OA_RECEIVED`, `PHONE_SCREEN` or `REJECTED` automatically. A macOS notification fires on OA and phone-screen signals.

## If something's off

| Symptom | Fix |
|---|---|
| `ingest` says no CSVs found | Run your scraper first, or pass a path: `cockpit ingest path/to/file.csv` |
| Popup does nothing | Reload tmux (step 1) and check that `~/.local/bin/uv` exists |
| `sync-mail` fails in the background | Read `/tmp/jobcockpit.err.log`. It is usually `GMAIL_USER` missing from `~/.zshenv` |
| Status bar blank | Run `cockpit ingest` once, or `cockpit tmux-status`, to create the cache file |
| `p` shows no preview | The PDF isn't at the configured path |

## Known limits

- The TUI by hand, the real Gmail connection, and the launchd agent have not been run end to end. Do a first pass with 2–3 jobs before a full sprint.
- Mail matching is by company name in the email subject or sender, so a vague subject ("Your application update") won't match and will be missed.
- Scoring weights in `config/scoring_rules.toml` are starting guesses. Tune them against real results.
