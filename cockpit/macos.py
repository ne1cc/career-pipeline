from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

KEYCHAIN_SERVICE = "jobcockpit-gmail"


def posix_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def copy_to_clipboard(text: str) -> None:
    data = text.encode("utf-8")
    try:
        subprocess.run(["/usr/bin/pbcopy"], input=data, check=True)
    except (FileNotFoundError, OSError):
        for fallback in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
            try:
                subprocess.run(fallback, input=data, check=True)
                return
            except (FileNotFoundError, OSError, subprocess.CalledProcessError):
                continue


def copy_resume_path(resume_path: str) -> str:
    """Put the absolute POSIX path on the clipboard, ready for Cmd+Shift+G in a file dialog."""
    path = posix_path(resume_path)
    copy_to_clipboard(path)
    return path


def open_url(url: str, background: bool = False) -> None:
    cmd = ["/usr/bin/open", "-g", url] if background else ["/usr/bin/open", url]
    try:
        subprocess.run(cmd, check=True)
    except (FileNotFoundError, OSError):
        try:
            subprocess.run(["xdg-open", url], check=False)
        except (FileNotFoundError, OSError):
            pass


def quicklook(resume_path: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["/usr/bin/qlmanage", "-p", posix_path(resume_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def notify_macos(title: str, message: str) -> None:
    """Send a native macOS notification via osascript. Safe no-op on non-macOS or failure."""
    if sys.platform != "darwin":
        return
    try:
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(message)}" with title "{esc(title)}"'
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False, capture_output=True)
    except Exception:
        pass


def notify(message: str, title: str = "JobCockpit") -> None:
    notify_macos(title=title, message=message)


KEYCHAIN_GEMINI_SERVICE = "jobcockpit-gemini"


def open_file(path: str | Path) -> None:
    try:
        subprocess.run(["/usr/bin/open", str(path)], check=True)
    except (FileNotFoundError, OSError):
        try:
            subprocess.run(["xdg-open", str(path)], check=False)
        except (FileNotFoundError, OSError):
            pass


def keychain_password(service: str = KEYCHAIN_SERVICE, env_var: str = "GMAIL_APP_PASSWORD") -> str | None:
    """Password from login Keychain, falling back to an environment variable."""
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, OSError):
        pass
    return os.getenv(env_var) or None


def gemini_api_key() -> str | None:
    return keychain_password(KEYCHAIN_GEMINI_SERVICE, "GEMINI_API_KEY")
