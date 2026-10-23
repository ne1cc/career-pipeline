#!/usr/bin/env bash
# ==============================================================================
# setup.sh — Turnkey Installer & Bootstrapper for Career Pipeline + Cockpit
# Usage:
#   ./setup.sh                      # Full setup (venv, Chromium, seed templates, build resumes, link CLI)
#   ./setup.sh --onboard            # Also launch interactive `cockpit onboard` wizard
#   ./setup.sh --from-resumes PATH  # Import existing resume(s) during setup
#   ./setup.sh --no-browser         # Skip Playwright Chromium browser download (fast CI mode)
#   ./setup.sh --no-shell           # Skip modifying ~/.zshrc / ~/.bashrc or ~/bin symlinks
# ==============================================================================
set -euo pipefail

# Lift shell file-size limits (avoids SIGXFSZ 25 on 40-50MB wheels like polars/playwright in restricted shells)
ulimit -f unlimited 2>/dev/null || ulimit -f $(ulimit -H -f 2>/dev/null || echo 10000000) 2>/dev/null || true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

INSTALL_BROWSER=1
INSTALL_SHELL=1
RUN_ONBOARD=0
FROM_RESUMES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-browser)
            INSTALL_BROWSER=0
            shift
            ;;
        --no-shell)
            INSTALL_SHELL=0
            shift
            ;;
        --onboard)
            RUN_ONBOARD=1
            shift
            ;;
        --from-resumes)
            FROM_RESUMES="${2:-}"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: ./setup.sh [--onboard] [--from-resumes <path>] [--no-browser] [--no-shell]" >&2
            exit 1
            ;;
    esac
done

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "==> [1/6] Checking uv package manager..."
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> [2/6] Creating Python 3.12 virtual environment (.venv) & installing dependencies..."
uv venv --python 3.12 "$REPO_ROOT/.venv" --allow-existing
if ! uv pip install --python "$REPO_ROOT/.venv/bin/python" -r "$REPO_ROOT/cockpit/requirements.txt" pytest; then
    echo "[!] Full dependency install hit a resource or cache limit. Retrying with --no-cache..."
    ulimit -f unlimited 2>/dev/null || true
    uv pip install --no-cache --python "$REPO_ROOT/.venv/bin/python" -r "$REPO_ROOT/cockpit/requirements.txt" pytest
fi

if [[ "$INSTALL_BROWSER" -eq 1 ]]; then
    echo "==> [3/6] Installing Playwright Chromium for 1-page PDF rendering..."
    "$REPO_ROOT/.venv/bin/python" -m playwright install chromium || echo "[!] Warning: Playwright Chromium install failed; minimal PDF fallback will be used."
else
    echo "==> [3/6] Skipping Playwright Chromium install (--no-browser)."
fi

echo "==> [4/6] Seeding user-layer templates, initializing DuckDB, and compiling resumes..."
chmod +x "$REPO_ROOT/bin/cockpit" "$REPO_ROOT/bin/tmux-jobcockpit"
"$REPO_ROOT/.venv/bin/python" -m cockpit.cli init
if [[ -n "$FROM_RESUMES" ]]; then
    "$REPO_ROOT/.venv/bin/python" -m cockpit.cli onboard --from-resumes "$FROM_RESUMES"
else
    "$REPO_ROOT/.venv/bin/python" -m cockpit.cli onboard --seed-defaults
fi

if [[ "$INSTALL_SHELL" -eq 1 ]]; then
    echo "==> [5/6] Linking CLI scripts and shell helpers..."
    BIN_DIR="$HOME/bin"
    mkdir -p "$BIN_DIR"
    ln -sf "$REPO_ROOT/bin/cockpit" "$BIN_DIR/cockpit"
    ln -sf "$REPO_ROOT/bin/tmux-jobcockpit" "$BIN_DIR/tmux-jobcockpit"

    SOURCE_LINE="source \"$REPO_ROOT/shell/cockpit.sh\""
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
        if [[ -f "$rc" ]] && ! grep -Fq "shell/cockpit.sh" "$rc"; then
            printf "\n# Career Pipeline & Cockpit shortcuts\n%s\n" "$SOURCE_LINE" >> "$rc"
            echo "  • Added cockpit.sh source line to $rc"
        fi
    done
else
    echo "==> [5/6] Skipping shell/symlink modifications (--no-shell)."
fi

echo "==> [6/6] Running cockpit doctor..."
"$REPO_ROOT/.venv/bin/python" -m cockpit.cli doctor

if [[ "$RUN_ONBOARD" -eq 1 ]]; then
    "$REPO_ROOT/.venv/bin/python" -m cockpit.cli onboard
else
    echo ""
    echo "✅ Setup complete!"
    echo "Next steps to personalize with your own resumes and profile:"
    echo "  1. Import your existing resume(s):  cockpit onboard --from-resumes /path/to/your/resume.pdf"
    echo "  2. Or run the interactive wizard:   cockpit onboard"
    echo "  3. Launch the TUI:                  cockpit"
fi
