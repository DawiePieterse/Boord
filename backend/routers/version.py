from fastapi import APIRouter

from version import version_info

router = APIRouter(prefix="/api/version", tags=["version"])


@router.get("")
def get_version():
    """What release this server is running.

    Deliberately public, like /api/system-settings. Two readers need it and
    neither can hold an admin token: the heartbeat script, which runs as
    SYSTEM every ten minutes, and whoever is standing in front of a farm
    trying to work out why a screen looks wrong. It reports a tag, a schema
    revision and a backup date - nothing about a person, and nothing that
    isn't already visible in the page header of every app.
    """
    return version_info()
