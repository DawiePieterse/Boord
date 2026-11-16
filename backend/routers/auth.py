"""Signing in and pairing devices - see pairing.py for what and why.

Three calls are open to anybody, because they are how a device with nothing
gets something: state (what the sign-in page should show), login (the PIN)
and pair (a one-time code). Everything else here is the office managing its
devices, and is admin-only like the rest of Settings.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import SQLModel

import pairing
from security import is_admin_client, may_pair_an_owner, paired_device, require_admin_client

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PinIn(SQLModel):
    pin: str


class CodeIn(SQLModel):
    code: str


class PairingIn(SQLModel):
    role: str
    label: str = ""


OWNER_ONLY_MESSAGE = "Only the owner can add or remove an owner's device"
PIN_OWNER_ONLY_MESSAGE = "Only the owner can change the admin PIN"


def _kind_of_device(request: Request) -> str:
    """A word for the list in Settings, so the office can tell which line is
    the phone that was lost. Read off the browser's own description of
    itself: good enough to recognise a device by, and nothing rests on it."""
    agent = request.headers.get("user-agent", "")
    for name in ("iPhone", "iPad", "Android", "Windows", "Macintosh", "Linux"):
        if name in agent:
            return "Mac" if name == "Macintosh" else name
    return "Unknown device"


@router.get("/state")
def auth_state(request: Request):
    """What the sign-in page needs to decide what to show. Says nothing a
    stranger could use: whether a PIN exists, and what THIS device already is."""
    device = paired_device(request)
    return {
        "pin_set": pairing.pin_is_set(),
        "role": device["role"] if device else None,
        "admin": is_admin_client(request),
        "can_pair_owner": may_pair_an_owner(request),
        "locked_for_seconds": pairing.seconds_locked_out(),
    }


@router.post("/login")
def login(body: PinIn, request: Request, response: Response):
    locked = pairing.seconds_locked_out()
    if locked:
        raise HTTPException(429, "Too many wrong PINs - try again in %d minutes" % ((locked + 59) // 60))
    if not pairing.pin_is_set():
        # Said plainly, because the fix is not "try another PIN": the PIN is
        # set from an Admin app that is already open (over Tailscale, the
        # first time), in Settings > Devices.
        raise HTTPException(403, "No admin PIN has been set yet - set one in Admin > Settings > Devices")
    if not pairing.check_pin(body.pin):
        raise HTTPException(403, "That is not the admin PIN")
    device = pairing.pair(response, "admin", _kind_of_device(request))
    return {"role": device["role"]}


@router.post("/pair")
def pair_device(body: CodeIn, request: Request, response: Response):
    """A POST, and from a button on the pairing page, never the link itself.
    WhatsApp fetches a link to draw its preview the moment it is pasted,
    and a code that paired on GET would be spent on WhatsApp's server before
    the farm manager ever saw the message."""
    entry = pairing.redeem_pairing_code(body.code)
    if entry is None:
        raise HTTPException(403, "This pairing code has been used or has expired - ask the office for a new one")
    label = entry["label"] or _kind_of_device(request)
    device = pairing.pair(response, entry["role"], label)
    return {"role": device["role"]}


@router.put("/pin", dependencies=[Depends(require_admin_client)])
def set_pin(body: PinIn, request: Request):
    # The owner's to change, by the rule that guards an owner's pairing
    # (may_pair_an_owner: an owner's device, or one on Tailscale, which is
    # also how a farm sets its first PIN before anybody is paired). A device
    # that is admin only because it knows the PIN, or was handed an admin
    # link, could otherwise change it and lock the rest of the office out.
    if not may_pair_an_owner(request):
        raise HTTPException(403, PIN_OWNER_ONLY_MESSAGE)
    try:
        pairing.set_pin(body.pin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"pin_set": True}


@router.post("/pairings", dependencies=[Depends(require_admin_client)])
def create_pairing(body: PairingIn, request: Request):
    if body.role == "owner" and not may_pair_an_owner(request):
        raise HTTPException(403, OWNER_ONLY_MESSAGE)
    try:
        code = pairing.new_pairing_code(body.role, body.label)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"code": code, "role": body.role, "valid_hours": pairing.PAIRING_CODE_SECONDS // 3600}


@router.get("/devices", dependencies=[Depends(require_admin_client)])
def list_paired_devices(request: Request):
    device = paired_device(request)
    this_id = device["id"] if device else None
    return [dict(d, this_device=d["id"] == this_id) for d in pairing.paired_devices()]


@router.delete("/devices/{device_id}", dependencies=[Depends(require_admin_client)])
def revoke_paired_device(device_id: str, request: Request):
    # The office cannot lock the owner out any more than it can let itself
    # in: an owner's device is the owner's to remove.
    owners = {d["id"] for d in pairing.paired_devices() if d.get("role") == "owner"}
    if device_id in owners and not may_pair_an_owner(request):
        raise HTTPException(403, OWNER_ONLY_MESSAGE)
    if not pairing.revoke(device_id):
        raise HTTPException(404, "No such paired device")
    return {"revoked": device_id}
