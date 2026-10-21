# JobCockpit & Pipeline Shortcuts

Quick reference for all keyboard shortcuts, shell aliases, fast jumps, and CLI commands for the JobCockpit and Career Pipeline ecosystem.

---

## 1. Fast Shell Jumps & Aliases (Zsh)

Run directly from any terminal session (configured in `~/.zshrc`):

| Command | Does |
|---|---|
| `cjd` or `dossier` | Interactive `fzf` picker for all interview dossiers with syntax-highlighted preview (`bat`/`glow`) |
| `cjd <query>` | Jump directly to a company's dossier (e.g. `cjd nintendo` or `cjd stripe`) |
| `cjd -r` or `cjd --root` | `cd` into the artifacts root directory (`~/Documents/jobspy-cockpit`) |
| `cjd -o [query]` | Open matching dossier in default macOS viewer instead of `$EDITOR` |
| `cjd -f [query]` | Open matching job's artifacts folder in macOS Finder |
| `cjf` or `artifacts` | Open the artifacts root (`~/Documents/jobspy-cockpit`) in macOS Finder |
| `cjf <query>` | Open a specific company's artifacts folder in macOS Finder (e.g. `cjf nintendo`) |
| `cockpit` | Launch Cockpit TUI (or run subcommands below) |
| `jobspy-pipeline` | Run the complete end-to-end scraper pipeline with Excel build & email delivery |
| `jobspy-parallel` | Run parallel scraper scripts directly with launch staggering and auto-retry |

---

## 2. Terminal Commands (`cockpit`)

Run these using the `cockpit` alias or `python -m cockpit.cli`:

### Dossiers & Artifacts
| Command | Does |
|---|---|
| `cockpit dossier` | List all saved job dossiers in a table with file & PDF counts |
| `cockpit dossier <query>` | Open matching dossier in default editor/viewer (e.g. `cockpit dossier nintendo`) |
| `cockpit dossier <query> --cat` | Print full syntax-highlighted Markdown dossier in terminal |
| `cockpit dossier --latest` | Open the most recently updated dossier |
| `cockpit folder` / `cockpit artifacts` | Open `~/Documents/jobspy-cockpit` in macOS Finder |
| `cockpit folder <query>` | Open specific company's folder in macOS Finder (e.g. `cockpit folder nintendo`) |

### Pipeline & Ingestion
| Command | Does |
|---|---|
| `cockpit ingest` | Ingest scraped CSVs into DuckDB, deduplicate, score, and queue |
| `cockpit rescore` | Re-score all queued jobs against `config/scoring_rules.toml` with visual progress |
| `cockpit rescore --audit` | Rescore with detailed breakdown of disqualification reasons |
| `cockpit pipeline` | View live scraper monitoring dashboard |
| `cockpit pipeline run` | Spawn scraper pipeline in background and attach live monitor |
| `cockpit pipeline logs [scraper]` | Tail logs for master pipeline or specific scraper (e.g. `seattle`, `ats`) |
| `cockpit intel [--limit 20]` | Run batch AI evaluation and generate tailored dossiers + PDF exports |
| `cockpit sync-saved` | Reconcile saved jobs between disk and DuckDB, and rebuild `index.md` |
| `cockpit sync-mail` | Reconcile applied jobs against ATS status update emails |
| `cockpit stats` | View funnel conversion metrics by archetype and board |
| `cockpit tmux-status` | Output cached status line for tmux status bar (`Q:..., 72h:...`) |
| `cockpit keychain-setup --run` | Store Gmail app password in macOS Keychain |
| `cockpit install-agent` | Install the 08:30 / 13:30 launchd background sync agent |

---

## 3. Inside the Cockpit TUI

### Open / Close
| Keys | Does |
|---|---|
| `Alt+Shift+j` | Open the cockpit popup from any tmux pane (no prefix) |
| `q` | Quit the cockpit (closes the popup) |
| `Prefix` then `Esc` | Force-close any tmux popup |

### Move & Filter
| Key | Does |
|---|---|
| `j` / `↓` | Next job |
| `k` / `↑` | Previous job |
| `J` / `K` | Scroll JD preview pane down / up |
| `d` / `u` | Page JD preview pane half-page down / up |
| `t` | Toggle 72h recent filter (toggle between `72H` and `QUEUED`) |
| `f` | Cycle view filter: `QUEUED` → `72H` → `SAVED` → `REFERRAL_HOLD` → `APPLIED` → `All` |
| `g` | Cycle scraper segment: `all` → `seattle` → `west_coast` → `remote` → … |
| `/` | Filter by location text (`wa`, `seattle`, `remote`); blank clears |
| `?` | Search jobs by keyword |

### Dossiers, Artifacts & Application
| Key | Does |
|---|---|
| `b` | Bookmark / save job and generate full AI packaging & dossier |
| `i` | Open the generated interview dossier (`05_interview_dossier.md`) |
| `I` | Re-queue and re-run AI intel evaluation |
| `F` | Open the job's artifact folder in Finder (contains all 8 MD files & exports PDF/DOCX) |
| `c` | Copy tailored cover letter path to clipboard |
| `o` or `Enter` | Copy tailored resume path to clipboard **and** open apply URL |
| `O` (Shift+o) | Same as `o`, but opens in a background browser tab |
| `p` | QuickLook preview of the staged resume |
| `1` / `2` / `3` | Force resume archetype (`swe_backend`, `data_eng`, `analytics_msba`) and re-copy path |

### Record Outcome & Follow Up
| Key | Does |
|---|---|
| `a` | Mark **Applied** and advance to next job |
| `s` | Mark **Skipped** and advance |
| `h` | Mark **Referral Hold**; type contact or note, `Enter` to save, `Esc` to cancel |
| `z` | Undo last state change |
| `n` | Edit personal notes on selected job |
| `r` | Open LinkedIn company referral search |
| `y` | Copy tailored outreach note (or template) to clipboard |
| `R` | Sync Gmail statuses immediately |

---

## 4. The Per-Application Rhythm

1. `Alt+Shift+j` to open Cockpit popup.
2. `j` / `k` to browse target jobs.
3. `b` to bookmark and generate the tailored application package & dossier.
4. `i` to review the dossier (`05_interview_dossier.md`) or `F` to inspect generated PDFs in Finder.
5. `o` to copy tailored resume path to clipboard and open the application page in your browser.
6. In file picker: `Cmd+Shift+G` → `Cmd+V` → `Enter` `Enter` to upload resume.
7. `c` to copy cover letter path if needed.
8. Submit application, return to Cockpit, and press `a` to mark applied.
9. Press `y` to copy tailored outreach pitch note to recruiter/engineer.
