"""Pairing devices: the half of the paired-device cookie only Boord does.

device_auth.py (the same file in every app) checks a cookie. This module is
what makes one: it keeps the farm's signing secret, the admin PIN, the
one-time pairing codes and the list of what has been paired. Boord is the
hub, so it is the one app with these screens - the others only check.

Four kinds of device, one mechanism:

  admin      signs in with the farm's PIN, once per device.
  owner      everything the admin has, and the two apps that are the owner's
             alone: Boord Owner and Boord Notes. Paired by a link, never the
             PIN - the office knows the PIN, and knowing it must not open
             the owner's figures or a private notebook. Only an owner (or a
             device on Tailscale, which could always open both) can make one.
  dashboard  the farm manager: opens a pairing link the office sent. Gets
             the Dashboard tab and nothing else (security.py).
  field      a tablet or phone in the orchard or the pack house: scans a
             pairing QR. No PIN for a picker to know or pass around.

Everything is a small text file in data/, beside dashboard_only.txt, and for
the same reason: one office, a handful of devices, and a person who can open
a file in Notepad over AnyDesk when something has gone wrong. No table, so no
migration - this arrived in the middle of a harvest, and the migration path
is the one part of a release that can stop a farm server booting.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import device_auth
from db import DATA_DIR

# device_auth.py looks for Boord's data folder beside the app it is running
# in. Here that IS this app, wherever the folder happens to be called (a
# worktree, a clone named anything), so say so once. setdefault, so a test
# or an unusual install that points it elsewhere still wins.
os.environ.setdefault(device_auth.AUTH_DIR_ENV, DATA_DIR)

ROLES = ("admin", "owner", "dashboard", "field")
# What a pairing code may stand for: every role. "admin" was left out at
# first, on the grounds that an admin signs in by knowing the PIN - but the
# farm wanted to add the office person's phone without telling them the PIN
# (and without them typing it where somebody can watch). Only an admin or an
# owner can make a code at all (routers/auth.py), so an admin link hands
# nobody more than its maker already had.
PAIRABLE_ROLES = ("admin", "owner", "dashboard", "field")

PIN_FILE = "admin_pin.txt"
PAIRED_FILE = "device_paired.txt"
# The Domain the cookie is set for, e.g. "bekfontein.co.za", so one sign-in
# covers boord.<domain>, kudde.<domain> and the rest. Written by hand, once,
# when the farm gets its domain. Deliberately not guessed from the Host
# header: "drop the first label" turns boord.co.za into co.za, which every
# browser refuses, and the symptom would be a sign-in that silently does not
# stick. Absent, the cookie belongs to the one address it was set on.
COOKIE_DOMAIN_FILE = "device_cookie_domain.txt"

MIN_PIN_LENGTH = 4
_PBKDF2_ROUNDS = 600_000

# A pairing link may be sent by WhatsApp to somebody who opens it tonight.
PAIRING_CODE_SECONDS = 24 * 3600

# Wrong PINs before the door shuts, and for how long. Counted for the whole
# farm rather than per address: there is one PIN, and an attacker on the
# internet has as many addresses as they care to use.
MAX_WRONG_PINS = 5
LOCKOUT_SECONDS = 5 * 60

_lock = threading.Lock()
# ponytail: pairing codes and the wrong-PIN count live in memory, so a
# restart forgets them - an unused pairing link dies with the nightly update
# and the office makes another. A file if that ever costs anybody anything.
_codes = {}            # code -> {"role", "label", "expires"}
_wrong_pins = 0
_locked_until = 0.0


def _path(name: str) -> str:
    return os.path.join(os.environ[device_auth.AUTH_DIR_ENV], name)


def _read(name: str) -> str:
    try:
        with open(_path(name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write(name: str, text: str, append: bool = False) -> None:
    os.makedirs(os.path.dirname(_path(name)), exist_ok=True)
    with open(_path(name), "a" if append else "w", encoding="utf-8") as f:
        f.write(text)


# --- The signing secret ----------------------------------------------------

def _secret() -> bytes:
    """The farm's secret, made the first time anything needs signing. Until
    then there is no file, and every app refuses every cookie."""
    existing = _read(device_auth.SECRET_FILE).strip()
    if len(existing) >= 32:
        return existing.encode()
    fresh = secrets.token_urlsafe(48)
    _write(device_auth.SECRET_FILE, fresh + "\n")
    return fresh.encode()


# --- The admin PIN ---------------------------------------------------------

def pin_is_set() -> bool:
    return bool(_read(PIN_FILE).strip())


def set_pin(pin: str) -> None:
    if len(pin) < MIN_PIN_LENGTH:
        raise ValueError("The PIN needs at least %d characters" % MIN_PIN_LENGTH)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PBKDF2_ROUNDS)
    _write(PIN_FILE, "pbkdf2_sha256$%d$%s$%s\n" % (_PBKDF2_ROUNDS, salt.hex(), digest.hex()))


def _pin_matches(pin: str) -> bool:
    try:
        scheme, rounds, salt, digest = _read(PIN_FILE).strip().split("$")
        if scheme != "pbkdf2_sha256":
            return False
        attempt = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(attempt, bytes.fromhex(digest))
    except ValueError:
        return False  # no PIN set, or a file somebody edited into nonsense


def seconds_locked_out(now: Optional[float] = None) -> int:
    return max(0, int(_locked_until - (time.time() if now is None else now) + 0.999))


def check_pin(pin: str, now: Optional[float] = None) -> bool:
    """Whether this is the farm's PIN - and False, whatever was typed, while
    the door is shut. Shutting it can lock the real admin out of a NEW
    sign-in for five minutes; devices already signed in are untouched, which
    is why that is a price worth paying for a PIN nobody can grind through."""
    global _wrong_pins, _locked_until
    now = time.time() if now is None else now
    with _lock:
        if now < _locked_until:
            return False
        if _pin_matches(pin):
            _wrong_pins = 0
            return True
        _wrong_pins += 1
        if _wrong_pins >= MAX_WRONG_PINS:
            _wrong_pins = 0
            _locked_until = now + LOCKOUT_SECONDS
        return False


# --- Pairing codes ---------------------------------------------------------

def new_pairing_code(role: str, label: str, now: Optional[float] = None) -> str:
    """A one-time code the office hands to a device, as a QR or a link. The
    PIN itself is never in it: an admin link makes an admin device, and the
    PIN stays with whoever knows it."""
    if role not in PAIRABLE_ROLES:
        raise ValueError("A pairing code is for a field, dashboard, admin or owner device")
    now = time.time() if now is None else now
    code = secrets.token_urlsafe(24)
    with _lock:
        for old in [c for c, entry in _codes.items() if entry["expires"] <= now]:
            del _codes[old]
        _codes[code] = {"role": role, "label": label.strip()[:60], "expires": now + PAIRING_CODE_SECONDS}
    return code


def redeem_pairing_code(code: str, now: Optional[float] = None) -> Optional[dict]:
    """The role and label a code stood for - once. A second use finds nothing,
    so a link that was forwarded on pairs one device, not two."""
    now = time.time() if now is None else now
    with _lock:
        entry = _codes.pop(code, None)
    if entry is None or entry["expires"] <= now:
        return None
    return entry


# --- Paired devices --------------------------------------------------------

def _cookie_domain() -> Optional[str]:
    domain = _read(COOKIE_DOMAIN_FILE).split("#")[0].strip().lstrip(".").lower()
    return domain or None


def link_origin() -> Optional[str]:
    """Where a pairing link must open, when the farm has a domain of its own.

    The link used to go to wherever Admin happened to be open. But an owner's
    link can only be made by an owner's device or from Tailscale, so it was
    made from Tailscale, and came out with the Tailscale address: a phone
    without Tailscale could not find the server at all, and one with it paired
    at an address where the cookie cannot be given the farm's domain, so
    Owner and Notes went on refusing it (Bekfontein, 17 Nov 2026). The pairing
    has to happen at the farm's address whichever way the office came in.

    "boord." because that is the name every farm's tunnel gives this app - the
    file holds the domain, not a hostname, since the cookie needs the domain.
    None without the file: the caller falls back to its own address."""
    domain = _cookie_domain()
    return "https://boord." + domain if domain else None


def pair(response, role: str, label: str) -> dict:
    """Remember a new device and hand its browser the cookie."""
    if role not in ROLES:
        raise ValueError("Unknown role %r" % role)
    device = {
        "id": secrets.token_hex(8), "role": role, "label": label.strip()[:60],
        "paired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # exp is filled in by device_auth.renew, which is the one place that
    # knows how the cookie is set - a first cookie is a renewal from nothing.
    payload = {"id": device["id"], "role": role, "exp": 0}
    domain = _cookie_domain()
    if domain:
        payload["dom"] = domain
    _secret()  # make sure there is one before renew() goes looking
    device_auth.renew(response, payload)
    _write(PAIRED_FILE, json.dumps(device) + "\n", append=True)
    return device


def paired_devices() -> list:
    """Every device still paired, newest first. The list is what the office
    reads to decide which line to revoke, so a revoked device is not on it."""
    revoked = device_auth._revoked_ids()
    devices = []
    for line in _read(PAIRED_FILE).splitlines():
        try:
            device = json.loads(line)
        except ValueError:
            continue  # a line somebody edited by hand: it names no device
        if isinstance(device, dict) and device.get("id") and device["id"] not in revoked:
            devices.append(device)
    return devices[::-1]


def revoke(device_id: str) -> bool:
    """Cut one device off, in every app, from its next request. Its line goes
    into the file all seven apps read; nothing else has to be told."""
    for device in paired_devices():
        if device["id"] == device_id:
            note = "%s, %s" % (device.get("label") or "no name", device.get("role", ""))
            _write(device_auth.REVOKED_FILE, "%s  # %s\n" % (device_id, note.replace("\n", " ")), append=True)
            return True
    return False
