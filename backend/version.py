"""What release this server is actually running.

Until now nothing on the server could answer that. The only version anywhere
was Boord.VERSION in frontend/shared/api.js, painted into the three app
headers - which tells you what a *browser* loaded, not what the server is
serving, and those disagree exactly when it matters (a device holding a
cached copy of the previous release). There was no way to ask a farm which
release it was on without asking a person to read a screen.

The version is not invented here. scripts/release.sh already refuses to sign
a tag whose Boord.VERSION disagrees with it, so on any checkout that came
from a release those two agree by construction. What was missing was a
runtime, server-side link to the git tag - and a way to see when that
correspondence has been broken by hand.

Four states, in order of preference, all reported honestly rather than
collapsed into one string:

    release   git describe --exact-match found a signed release tag
    ahead     a checkout between releases, e.g. v3.0-4-gb1182b3 (the dev Mac)
    reported  no usable git, but update_server.bat left the tag it installed
    unknown   none of the above

frontend_version is parsed from api.js in every state, with the same anchored
expression release.sh uses. That is the floor: it works with no git, no tags
and no data/ directory, so the endpoint always says something true.

Nothing in here raises. A version endpoint that can 500 is worse than no
version endpoint, because it fails on exactly the unhealthy servers you most
want to hear from.
"""
import json
import os
import re
import shutil
import subprocess
from typing import Optional

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
API_JS = os.path.join(REPO_ROOT, "frontend", "shared", "api.js")
INSTALLED_VERSION_FILE = os.path.join(REPO_ROOT, "data", "installed_version.txt")
UPDATE_STATUS_FILE = os.path.join(REPO_ROOT, "data", "update_available.json")

# The same expression scripts/release.sh:49 greps with. Kept character for
# character on purpose: if the two ever disagree about what the version line
# looks like, the release gate and this endpoint would report different
# versions for the same file, which is worse than either being absent.
_VERSION_RE = re.compile(r'^  VERSION: "(.*)",$', re.MULTILINE)

_GIT_TIMEOUT = 5


def _run_git(args: list) -> tuple:
    """(ok, stdout, stderr). Never raises, never blocks for long.

    The server runs as SYSTEM via a Scheduled Task, which makes two failures
    likely that would not happen in a console:

    - git may not be on SYSTEM's PATH, so it is looked up explicitly.
    - git refuses a repository owned by another user with "detected dubious
      ownership in repository", exits 128, and otherwise looks exactly like
      "git is not installed". That is why stderr is captured and surfaced -
      the two need different fixes, and guessing wrong costs an afternoon.
    """
    git = shutil.which("git")
    if not git:
        return False, "", "git not found on PATH"
    try:
        proc = subprocess.run(
            [git] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            # No console window when the server runs as a Scheduled Task.
            # getattr because the flag only exists on Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, "", f"{type(e).__name__}: {e}"
    return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()


def _declared_version() -> Optional[str]:
    """Boord.VERSION as written in frontend/shared/api.js, or None."""
    try:
        with open(API_JS, "r", encoding="utf-8") as fh:
            match = _VERSION_RE.search(fh.read())
    except OSError:
        return None
    return match.group(1) if match else None


def _installed_version() -> Optional[str]:
    """The tag update_server.bat last checked out, if it left a note.

    Fallback only. On any server that can actually update itself git is the
    truth; this covers a copy taken without .git, and the window before the
    first update_server.bat run on a fresh clone.
    """
    try:
        with open(INSTALLED_VERSION_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        return None
    return None


def _git_version() -> tuple:
    """(tag, describe, state, git_error) from git alone."""
    ok, out, err = _run_git(["describe", "--tags", "--exact-match", "--match", "v*", "HEAD"])
    if ok and out:
        return out, out, "release", None

    # Not on a tag is the normal case between releases, and its stderr is
    # noise ("no tag exactly matches..."). Only a describe that fails for
    # some *other* reason is worth reporting, which the second call decides.
    ok, out, err = _run_git(["describe", "--tags", "--match", "v*", "--always", "--dirty"])
    if ok and out:
        return None, out, "ahead", None

    return None, None, None, (err[:200] or None)


def _alembic() -> tuple:
    """(head, current). Imported here rather than at module import so a
    broken database cannot stop this module loading."""
    head = current = None
    try:
        import migrate
        head = migrate.head_revision()
    except Exception:
        pass
    try:
        import migrate
        current = migrate.current_revision()
    except Exception:
        pass
    return head, current


def _backup_summary() -> dict:
    """A date and a count. Deliberately no filenames and nothing about
    people: this is the one part of the payload that leaves the farm in the
    heartbeat, and "the backup thread died three weeks ago" is the whole
    reason it is worth sending."""
    last = None
    count = 0
    try:
        import backup
        state = backup._read_state() or {}
        created = state.get("created_at")
        if isinstance(created, str):
            last = created[:10]
        count = len(backup._backup_filenames())
    except Exception:
        pass
    return {"last": last, "count": count}


def _update_status() -> Optional[dict]:
    """What the last `update_server.bat --check` found, if the check task is
    set up on this machine.

    Written by a different process from this one, and read live rather than
    cached - the whole point is that a check which ran an hour ago shows up
    without restarting the server. A missing file means nobody has set the
    check up, which is not an error and must not read as one.
    """
    try:
        with open(UPDATE_STATUS_FILE, "r", encoding="utf-8") as fh:
            status = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    return {
        "checked_at": status.get("checked_at"),
        "current": status.get("current"),
        "latest": status.get("latest"),
        "signature": status.get("signature"),
        "available": bool(status.get("update_available")),
    }


_static: Optional[dict] = None


def _compute_static() -> dict:
    tag, describe, state, git_error = _git_version()
    frontend_version = _declared_version()
    installed = _installed_version()

    if state is None:
        if installed:
            tag, describe, state = installed, installed, "reported"
        else:
            state = "unknown"

    # What a human should be told this server is running.
    version = (tag[1:] if tag and tag.startswith("v") else tag) or frontend_version or describe

    # Only meaningful on a release checkout. Between releases the tag is
    # behind api.js by design (ship.sh bumps the constant in the commit it
    # then tags), so comparing them there would report a fault that isn't one.
    matches = None
    if state == "release" and tag and frontend_version:
        matches = tag.lstrip("v") == frontend_version

    head, _ = _alembic()
    return {
        "version": version,
        "tag": tag,
        "describe": describe,
        "state": state,
        "frontend_version": frontend_version,
        "matches": matches,
        "git_error": git_error,
        "alembic_head": head,
    }


def prime() -> None:
    """Work the git/file lookups out once, at startup."""
    global _static
    _static = _compute_static()


def version_info() -> dict:
    """The whole picture. Safe to call on every request.

    The git side is cached from startup on purpose, and not only because
    subprocesses are expensive: it reports what this *process* booted from.
    So a checkout done by hand without a restart shows the server on v3.0
    while a reloaded browser header shows v3.1, and that disagreement is the
    signal - "the code changed but nothing restarted" is precisely the state
    that otherwise goes unnoticed for weeks.
    """
    global _static
    if _static is None:
        _static = _compute_static()
    info = dict(_static)
    # Live, because it is the one thing that can change under a running
    # server and the one worth alerting on: current behind head means the
    # migrations did not run.
    _, current = _alembic()
    info["alembic_current"] = current
    info["migrations_applied"] = (
        None if (current is None or info.get("alembic_head") is None)
        else current == info["alembic_head"]
    )
    info["backups"] = _backup_summary()
    info["update"] = _update_status()
    return info
