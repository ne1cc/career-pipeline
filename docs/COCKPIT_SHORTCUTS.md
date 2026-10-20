# JobCockpit shortcuts

## Open / close

| Keys | Does |
|---|---|
| `Alt+Shift+j` | Open the cockpit popup from any tmux pane (no prefix) |
| `q` | Quit the cockpit (closes the popup) |
| `Prefix` then `Esc` | Force-close any tmux popup (your existing binding) |

## Inside the cockpit

### Move

| Key | Does |
|---|---|
| `j` / `↓` | Next job |
| `k` / `↑` | Previous job |
| `f` | Cycle view: Queued → Referral Hold → Applied → All |
| `g` | Cycle scraper sheet: all → each sheet (seattle, west_coast, remote, …) |
| `/` | Filter by location text (`wa`, `seattle`, `remote`); blank clears |

### Apply

| Key | Does |
|---|---|
| `o` or `Enter` | Copy resume path to clipboard **and** open the application page |
| `O` (Shift+o) | Same, but opens in a background tab (focus stays in the terminal) |
| `p` | QuickLook preview of the staged resume (close with `Esc` or `Space`) |
| `1` | Use the `swe_backend` resume and re-copy its path |
| `2` | Use the `data_eng` resume and re-copy its path |
| `3` | Use the `analytics_msba` resume and re-copy its path |

### Record the outcome

| Key | Does |
|---|---|
| `a` | Mark **Applied** and advance to the next job |
| `s` | Mark **Skipped** and advance |
| `h` | Mark **Referral Hold**; type a contact or note, `Enter` to save, `Esc` to cancel |

### Follow up

| Key | Does |
|---|---|
| `y` | Copy the outreach message for the selected job |
| `R` | Sync Gmail now (marks OA, phone screen, rejected) |
| `Ctrl+P` | Textual command palette |

## The upload dialog (in any browser)

After `o`, when the file picker opens:

| Keys | Does |
|---|---|
| `Cmd+Shift+G` | Open "Go to folder" |
| `Cmd+V` | Paste the resume path |
| `Enter`, `Enter` | Go to the file, then confirm the upload |

## Status bar

`Jobs: 6/15 | Q:34 | H:2` means 6 applied today (Pacific time) out of 15, 34 queued above the score floor, and 2 on referral hold.

## Terminal commands

Run these with the `cockpit` alias.

| Command | Does |
|---|---|
| `cockpit tui` | Open the cockpit (same as `Alt+Shift+j`) |
| `cockpit ingest` | Import this month's scraped CSVs, dedupe and score |
| `cockpit rescore` | Re-apply `scoring_rules.toml` to queued jobs |
| `cockpit sprint --limit 15 [--segment seattle] [--location wa]` | Plain prompt loop: `a` apply, `s` skip, `h` hold, `q` quit |
| `cockpit sync-mail` | Reconcile applied jobs against ATS emails |
| `cockpit stats` | Conversion by resume archetype and job board |
| `cockpit tmux-status` | Print the status-bar line |
| `cockpit keychain-setup --run` | Store the Gmail app password in Keychain |
| `cockpit install-agent` | Install the 08:30 / 13:30 launchd sync |

## Per-application rhythm

`Alt+Shift+j` → `j`/`k` to pick → `p` to check the resume → `o` → `Cmd+Shift+G` → `Cmd+V` → `Enter` `Enter` → submit → `a`
