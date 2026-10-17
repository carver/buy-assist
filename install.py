#!/usr/bin/env python3
"""Set up Buy Assistant to run on this machine, and keep it running.

Idempotent, so it's safe to re-run after a pull or a sandbox recreate. It
- installs the locked dependencies (uv sync),
- checks the Purelymail credentials alert email needs are in /etc/sandbox-secrets.sh,
- makes sure a cron daemon is running,
- installs crontab entries that run scripts/serve.sh every 5 minutes and at boot, which
  starts the app (web on :6767 plus the hourly watch) whenever it isn't running.

`python3 install.py remove` takes the crontab entries out again; a running app keeps
running until stopped.
"""

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
MARKER = "# buy-assist"
_OURS = re.compile(rf"{re.escape(MARKER)}( |$)")


def cron_lines() -> list[str]:
    serve = f"bash {shlex.quote(str(REPO / 'scripts' / 'serve.sh'))}"
    return [
        f"*/5 * * * * {serve} {MARKER} keepalive",
        f"@reboot {serve} {MARKER} start at boot",
    ]


def merged_crontab(current: str, wanted: list[str]) -> str:
    """The crontab with this project's lines swapped for `wanted`, everyone else's kept."""
    others = [line for line in current.splitlines() if not _OURS.search(line)]
    return "\n".join([*others, *wanted]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("action", nargs="?", choices=["install", "remove"], default="install")
    args = parser.parse_args()
    if args.action == "remove":
        return 0 if set_crontab([]) else 1
    steps = [sync_dependencies, check_credentials, check_cron_daemon]
    ready = all([step() for step in steps])
    return 0 if ready and set_crontab(cron_lines()) else 1


def sync_dependencies() -> bool:
    result = subprocess.run(["uv", "sync", "--frozen", "--quiet"], cwd=REPO)
    print("✓ dependencies installed" if result.returncode == 0 else "✗ uv sync failed")
    return result.returncode == 0


def check_credentials() -> bool:
    """Checks the variables are set without ever reading their values into this process.
    Missing credentials don't stop the install: the app runs, it just sends no email."""
    check = (
        ". /etc/sandbox-secrets.sh 2>/dev/null; "
        '[ -n "${PURELYMAIL_ADDRESS:-}" ] && [ -n "${PURELYMAIL_PASSWORD:-}" ]'
    )
    if subprocess.run(["bash", "-c", check]).returncode == 0:
        print("✓ Purelymail credentials present")
    else:
        print(
            "! PURELYMAIL_ADDRESS / PURELYMAIL_PASSWORD missing from /etc/sandbox-secrets.sh;\n"
            "  the app will run but send no email. To fix, run this in a terminal:\n"
            "  python3 ~/.claude/skills/email/scripts/setup_credentials.py"
        )
    return True


def check_cron_daemon() -> bool:
    if subprocess.run(["pgrep", "-x", "cron"], capture_output=True).returncode == 0:
        print("✓ cron daemon running")
        return True
    if subprocess.run(["sudo", "/usr/sbin/cron"], capture_output=True).returncode == 0:
        print("✓ cron daemon started")
        return True
    print("✗ no cron daemon, and couldn't start one")
    return False


def set_crontab(wanted: list[str]) -> bool:
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if current.returncode != 0 and "no crontab" not in current.stderr:
        print(f"✗ couldn't read the crontab, so left it alone: {current.stderr.strip()}")
        return False
    text = merged_crontab(current.stdout if current.returncode == 0 else "", wanted)
    result = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"✗ crontab install failed: {result.stderr.strip()}")
        return False
    print(f"✓ crontab: {len(wanted)} buy-assist entries")
    return True


if __name__ == "__main__":
    sys.exit(main())
