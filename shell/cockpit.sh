# ==============================================================================
# shell/cockpit.sh — Career Pipeline & Cockpit Shell Helpers
# Source this file in ~/.zshrc or ~/.bashrc:
#   source "/path/to/career-pipeline/shell/cockpit.sh"
# ==============================================================================

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    _COCKPIT_SH_PATH="${BASH_SOURCE[0]}"
elif [[ -n "${ZSH_VERSION:-}" ]]; then
    _COCKPIT_SH_PATH="${(%):-%x}"
else
    _COCKPIT_SH_PATH="$0"
fi

export COCKPIT_REPO_ROOT="${COCKPIT_REPO_ROOT:-$(cd "$(dirname "$_COCKPIT_SH_PATH")/.." && pwd)}"
export PATH="$COCKPIT_REPO_ROOT/bin:$PATH"

# cjd [query] — cd directly into a saved job's dossier folder (or root export dir)
cjd() {
    local target
    if [[ $# -eq 0 ]]; then
        target="$(python3 -c 'from cockpit.intel import resolve_export_dir; print(resolve_export_dir())' 2>/dev/null || echo "$HOME/Documents/jobspy-cockpit")"
    else
        local export_root
        export_root="$(cd "$COCKPIT_REPO_ROOT" && cockpit folder --help >/dev/null 2>&1 && python3 -c 'from cockpit.intel import resolve_export_dir; print(resolve_export_dir())' 2>/dev/null || echo "$HOME/Documents/jobspy-cockpit")"
        target="$(find "$export_root" -maxdepth 1 -mindepth 1 -type d -iname "*$1*" | head -n 1)"
    fi
    if [[ -n "$target" && -d "$target" ]]; then
        cd "$target" || return 1
    else
        echo "No dossier folder found matching: ${1:-root}" >&2
        return 1
    fi
}

# cjn [query] — open a job's dossier files directly in Neovim ($EDITOR)
cjn() {
    cockpit edit "$@"
}

# cjf [query] — reveal a job's dossier folder in Finder / system file manager
cjf() {
    cockpit folder "$@"
}

# cjp [url|query] — generate ATS form prefill mappings and copy to clipboard
cjp() {
    cockpit prefill "$@" --copy
}

# cjv [resume] — audit resume for ATS parseability and keyword coverage
cjv() {
    cockpit verify-ats "$@"
}

# jobspy-pipeline — run the full JobSpy scraping + Excel + Cockpit ingest pipeline
jobspy-pipeline() {
    (
        cd "$COCKPIT_REPO_ROOT" || return 1
        if [[ -x "$COCKPIT_REPO_ROOT/.venv/bin/python" ]]; then
            "$COCKPIT_REPO_ROOT/.venv/bin/python" run_pipeline.py "$@"
        else
            uv run --no-project --python 3.12 --with-requirements "$COCKPIT_REPO_ROOT/cockpit/requirements.txt" python run_pipeline.py "$@"
        fi
    )
}

# jobspy-parallel — run the parallel scrapers standalone
jobspy-parallel() {
    (
        cd "$COCKPIT_REPO_ROOT" || return 1
        if [[ -x "$COCKPIT_REPO_ROOT/.venv/bin/python" ]]; then
            "$COCKPIT_REPO_ROOT/.venv/bin/python" run_parallel.py "$@"
        else
            uv run --no-project --python 3.12 --with-requirements "$COCKPIT_REPO_ROOT/cockpit/requirements.txt" python run_parallel.py "$@"
        fi
    )
}
