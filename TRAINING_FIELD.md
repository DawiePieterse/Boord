# Field Capture — Training Guide

For picking teams logging crates at a field station.

---

## Getting started with your phone

Field devices are **Ulefone RugKing 4 Pro** phones — rugged, high
battery capacity, built to last a full day in the field. When you're
given your phone, or at the start of each day:

1. **Check the battery.** Make sure there's enough charge for a full
   day. Charge it fully overnight if it's low.
2. **Unlock the phone.** Swipe up on the screen, then enter the code
   **1470**.
3. **Check Tailscale is connected.** Open the **Tailscale** app and look
   for a **green dot** next to your device's name — that means it's
   connected to the farm's account (**bekfontein01**). Without this,
   nothing in the Harvest app will work (see "If something looks wrong"
   further down).
4. **Open the app.** Tap the **Harvest Capture** icon on the home
   screen.

---

## The 5-step loop

This is everything you'll do, over and over, all day:

1. **Tap "Scan Worker QR"** and point the camera at the worker's badge.
2. **Choose the block** from the dropdown, if it isn't already selected.
3. **Type the weight** on the keypad.
4. **Tap "Save Crate."** You'll hear a chime and the crate count at the
   top of the screen goes up.
5. Repeat for the next crate.

When a truck is ready to take a load away, you send a **Picking Slip**
(see below) — that's the only other thing you'll do.

---

## Your screen, top to bottom

- **Top bar** — shows which station this device is, and a coloured pill
  telling you whether it's connected: **green** = everything is sent,
  **amber** = sending right now, **red** = no connection. Don't worry if
  it's red — see "Working without signal" below.
- **Amber bar under the top** — only appears when there's no connection,
  and says so plainly. It disappears on its own when the signal is back.
- **Crates / kg card** — how many crates and total kg logged for the
  load currently being built at this station, plus how long it's been
  running.
- **Scan Worker QR button** — tap this before every crate.
- **Block** — which block this crate came from.
- **Weight keypad** — type the number, then tap the crate icon (delete
  button clears a mistyped digit).
- **Save Crate** — logs the crate. Do this every single time, even if
  you're in a rush — a crate that isn't saved doesn't count.
- **Send Picking Slip** — only tap this when a truck is actually leaving
  with the load.

---

## Logging a crate, step by step

**1. Scan the worker's badge.**
Tap **Scan Worker QR**. Point the camera steadily at the worker's printed
badge until it beeps. The worker's name appears on screen once it's
matched — check it's the right person before continuing.

If it says **"QR code doesn't match a known worker,"** that badge isn't
recognized. Don't guess — tell your supervisor so the office can check or
reprint it. There's no way to type a worker in manually; it has to be a
scan.

If it says **"Camera unavailable,"** see "If something looks wrong" at
the end of this guide — it's usually a quick fix you can do yourself.

**2. Check the block.**
Make sure the block dropdown shows the block you're actually picking in.
It usually stays the same all day at one station, but double-check it
after a break or when moving to a new area.

**3. Weigh and enter the crate's weight.**
Use the on-screen keypad. The "." button is for decimals (e.g. `18.5`),
and the delete button (⌫) removes the last digit if you make a mistake.

**4. Save the crate.**
Tap **Save Crate**. You'll hear a short chime and the crate count updates
immediately. That's your confirmation it was logged — if you don't hear
the chime or see the count change, try again.

**5. Repeat.**
Scan the next worker for the next crate. You don't need to reselect the
block unless it's changed.

---

## Sending a Picking Slip (when the truck arrives)

When a truck is ready to take crates to the pack house:

1. Tap **Send Picking Slip**.
2. Enter how many crates are going **right now** — if the truck can't
   take everything you've logged, you can send fewer and keep the rest
   for the next truck (see "Splitting a load" below).
3. Enter the **driver's name**.
4. Tap **Send**.

A green **"Picking Slip Sent"** banner confirms it went through, and
you'll see it added to "Dispatched today from this station" further down
the screen.

### Splitting a load

If you have more crates logged than the truck can carry, you can send
just some of them — enter that smaller number of "crates going now"
instead of the full count. This needs the device to actually be
connected (not offline) at that moment, since the office's system has to
work out exactly which crates are going. If you see a message about
reconnecting to split the load, either wait a moment for the connection
to come back, or send everything on this truck instead.

---

## Working without signal

Out in the field, this device is often out of Wi-Fi/data range — that's
completely normal and nothing breaks. Every crate you save is stored on
the device itself first. As soon as the device gets a connection again
(even briefly), everything you logged sends itself to the office
automatically — you don't need to do anything.

The status at the top just tells you what's happening:

| Status | Colour | What it means |
|---|---|---|
| **Online - synced** | Green | Everything is up to date with the office. |
| **Syncing 3...** | Amber | Sending your logged crates now. |
| **Offline** | Red | No connection right now — crates are still saving on the device, nothing is lost. |
| **Offline - 3 pending** | Red | Same, and 3 crates are waiting to be sent. The number goes down by itself once you're back in range. |
| **Online - sync failed, retrying** | Amber | Reached the office system but it wouldn't accept the last send — it keeps trying on its own. |

You don't have to do anything about a red pill. Keep picking and keep
saving crates: the number just tells you how many are still waiting.

---

## Checking your own work

- **Recent crates** (lower on the screen) shows the last few crates you
  logged, so you can double-check nothing was missed.
- **Dispatched today from this station** shows every picking slip
  already sent today, with totals.
- **To refresh the screen**, drag down from the top and let go. A small
  spinner appears while it reloads. There's no need to do this normally
  — the screen keeps itself up to date — but it's there if you want to
  force it.

---

## If something looks wrong

- **"Camera unavailable" when scanning, or the status won't move past
  "Offline" even though the phone has signal?** Check that **Tailscale**
  is running:
  1. Open the **Tailscale** app on the phone (it's separate from this
     app, usually its own icon on the home screen).
  2. It should say **"Connected."** If it says "Not connected" or
     similar, tap to reconnect, or sign in again if it asks you to.
  3. Go back to this app and try again.

  This device needs Tailscale connected to reach the office system at
  all — without it, the camera scanner won't work and nothing will sync,
  even with full signal. If Tailscale shows Connected and it's still not
  working, that's one for your supervisor.
- **Logged the wrong weight or wrong worker on a crate that's already
  saved?** You can't fix this from the field device — tell your
  supervisor so the office can correct it.
- **Screen looks different / missing a button?** The app may need
  updating — tell your supervisor, don't try to fix it yourself.
- **Nothing happens when you tap Save Crate?** Make sure a weight is
  entered and a worker is selected first — both are needed before it'll
  save.
