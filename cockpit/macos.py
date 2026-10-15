from __future__ import annotations

import os
import subprocess
from pathlib import Path

KEYCHAIN_SERVICE = "jobcockpit-gmail"


def posix_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["/usr/bin/pbcopy"], input=text.encode("utf-8"), check=True)


def copy_resume_path(resume_path: str) -> str:
    """Put the absolute POSIX path on the clipboard, ready for Cmd+Shift+G in a file dialog."""
    path = posix_path(resume_path)
    copy_to_clipboard(path)
    return path


def open_url(url: str, background: bool = False) -> None:
    cmd = ["/usr/bin/open", "-g", url] if background else ["/usr/bin/open", url]
    subprocess.run(cmd, check=True)


def quicklook(resume_path: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["/usr/bin/qlmanage", "-p", posix_path(resume_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def notify(message: str, title: str = "JobCockpit") -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False)


def keychain_password(service: str = KEYCHAIN_SERVICE) -> str | None:
    """Gmail app password from the login Keychain, falling back to $GMAIL_APP_PASSWORD."""
    r = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return os.getenv("GMAIL_APP_PASSWORD") or None
