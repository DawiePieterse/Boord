"""Market invoices by email: the market agents' tax invoices, read straight
out of the farm's Gmail and filed against Boord Packhouse's delivery notes,
so the office no longer types each statement into Reports -> Markets &
Transport by hand.

The agents (Dapper at the Johannesburg market so far) email their invoices
to Andre. A copy reaches bekfontein01@gmail.com, either because the agent
lists that address too or because Andre's mail server forwards them. The farm
PC is tailnet-only, so nothing outside can push to it. Instead this thread
signs in to that Gmail over IMAP every FETCH_MINUTES with a Google app
password, reads every unhandled PDF in the inbox, and labels the message
DONE_LABEL in Gmail so it is never read twice. It keeps a copy of every
invoice it imports in data/market_mail/.

The invoice is printed by the market's own system (pps_invoice, the same
layout for every agent on that market), so a plain parser reads it. No AI,
nothing leaves the PC, and an invoice whose figures don't add up is refused
rather than half-imported. It names the delivery note ("PRODUCER D/NOTE:
1325"), which is Packhouse's note number, and that is the only link needed.
A note paid in parts gets an invoice per part; reports add them up.

Set up by one file, data/market_mail.txt (see MANUAL.md), never by the web
page, so the app password is only ever on the PC:

    address=bekfontein01@gmail.com
    app_password=abcd efgh ijkl mnop
    senders=dapper.co.za, laughw.co.za, rmputter.co.za

Onderberg's juice payment advice comes the same way (parse_onderberg), one
invoice per juice load, filed against Packhouse's J- note.

senders is the trust check: mail from any other domain is skipped and listed
on the Reports tab with who sent it, so a new agent can be added. Dapper's
domain doesn't enforce SPF or DKIM, so Gmail's verdict can't be relied on.
The real guard is that an invoice must parse, add up and name a note number.
Every import is listed with its invoice number and can be removed.
"""
import email
import imaplib
import io
import json
import os
import re
import threading
import time
from datetime import datetime
from email.utils import parseaddr
from typing import Optional

from sqlmodel import Session

from db import DATA_DIR, engine
from models import MarketInvoice

CONFIG_PATH = os.path.join(DATA_DIR, "market_mail.txt")
MAIL_DIR = os.path.join(DATA_DIR, "market_mail")
STATUS_PATH = os.path.join(MAIL_DIR, "status.json")
FETCH_MINUTES = 15
DONE_LABEL = "Boord-Done"
# Only mail this recent is looked at, so the first run on a busy inbox reads
# one season's worth, not years.
SEARCH = f'"has:attachment filename:pdf -label:{DONE_LABEL} newer_than:90d"'
MAX_PDF_BYTES = 5 * 1024 * 1024
DEFAULT_SENDERS = ["dapper.co.za", "laughw.co.za", "rmputter.co.za"]

_lock = threading.Lock()


def read_config() -> Optional[dict]:
    """data/market_mail.txt as a dict, or None while it isn't there or is
    missing the address or app password."""
    if not os.path.exists(CONFIG_PATH):
        return None
    cfg = {}
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                cfg[key.strip().lower()] = value.strip()
    if not cfg.get("address") or not cfg.get("app_password"):
        return None
    senders = [s.strip().lower().lstrip("@") for s in cfg.get("senders", "").split(",") if s.strip()]
    cfg["senders"] = senders or DEFAULT_SENDERS
    return cfg


# ---------------------------------------------------------------------------
# Reading the invoice
# ---------------------------------------------------------------------------

def pdf_text(data: bytes) -> str:
    """The PDF's text with its columns kept on their lines. pypdf's plain mode
    puts each column of the invoice in its own run, so labels and numbers no
    longer line up; layout mode keeps "QTY C/F:   179" together."""
    from pypdf import PdfReader  # only the mail thread needs it
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)


def _amount(s: str) -> float:
    return float(s.replace(",", ""))


def _find(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    if not m:
        raise ValueError(f"no {pattern!r} on the invoice")
    return m.group(1)


def split_invoices(text: str) -> list:
    """The invoices in one PDF. The agent sends a day's invoices as one batch,
    each page starting with its letterhead and invoice number; a page with the
    same number as the one before (a long invoice) carries it on."""
    agent = text.strip().splitlines()[0].strip()
    pages = re.split(r"(?m)^(?=[ \t]*" + re.escape(agent) + r"[ \t]*$)", text)
    invoices, last_no = [], None
    for page in pages:
        m = re.search(r"TAX INVOICE\s+(\d+/\d+)", page)
        if invoices and (not m or m.group(1) == last_no):
            invoices[-1] += page
        else:
            invoices.append(page)
            last_no = m and m.group(1)
    return [p for p in invoices if "TAX INVOICE" in p]


def parse_invoice(text: str) -> dict:
    """A market tax invoice (pps_invoice) as a dict, or ValueError saying what
    was missing or didn't add up. Deductions are taken including VAT, the way
    the office's workbook counted commission: they are what left the farm's
    money."""
    first_page = text.split("Batch Page", 1)[0]
    inv = {
        "invoice_no": _find(r"TAX INVOICE\s+(\d+/\d+)", text),
        "note_no": _find(r"PRODUCER D/NOTE:\s*(\S+)", text),
        "invoice_date": datetime.strptime(_find(r"\bDATE:\s*(\d{2}-[A-Z]{3}-\d{2})", text), "%d-%b-%y").date(),
        "payment": _find(r"(Final Payment|Partial Payment #\d+)", text),
        "agent": first_page.strip().splitlines()[0].strip(),
        "lines": [],
        "commission": 0.0, "deductions": 0.0, "bank_cost": 0.0,
        "gross": _amount(_find(r"SUM TOTAL SALES:\s*(-?[\d,]+\.\d\d)", text)),
        "net": _amount(_find(r"NETT INCOME:\s*(-?[\d,]+\.\d\d)", text)),
    }
    # One block per GRN (one product line on the truck). Sold is the cartons
    # paid on this invoice; the market prints it negative.
    for block in text.split("GRN NO:")[1:]:
        inv["lines"].append({
            "product": re.sub(r"\s+", " ", _find(r"PRODUCT:\s*(.+?)\s{2,}", block)).strip(),
            "sold": abs(int(_amount(_find(r"QTY PAID/SOLD:\s*(-?[\d,]+)", block)))),
            "income": _amount(_find(r"GRN INCOME:\s*(-?[\d,]+\.\d\d)", block)),
        })
    if not inv["lines"]:
        raise ValueError("no product lines on the invoice")

    details = text.split("DEDUCTION DETAILS", 1)[-1].split("DEDUCTION TOTAL", 1)[0]
    for m in re.finditer(r"^\s*([A-Za-z][A-Za-z .&/()-]*?)\s{2,}(-?[\d,]+\.\d\d)\s+(-?[\d,]+\.\d\d)\s+(-?[\d,]+\.\d\d)\s*$",
                         details, re.M):
        name, total = m.group(1).lower(), -_amount(m.group(4))
        key = "commission" if "commission" in name else "bank_cost" if "bank" in name else "deductions"
        inv[key] = round(inv[key] + total, 2)
    deducted = -_amount(_find(r"SUM TOTAL DEDUCTIONS:\s*(-?[\d,]+\.\d\d)", text))

    # The checks that stand in for trusting the sender: every rand accounted for.
    if abs(sum(ln["income"] for ln in inv["lines"]) - inv["gross"]) > 0.01:
        raise ValueError(f"product lines don't add up to the sales total {inv['gross']:.2f}")
    if abs(inv["commission"] + inv["deductions"] + inv["bank_cost"] - deducted) > 0.01:
        raise ValueError(f"deduction lines don't add up to {deducted:.2f}")
    if abs(inv["gross"] - deducted - inv["net"]) > 0.01:
        raise ValueError(f"sales less deductions isn't the nett income {inv['net']:.2f}")
    return inv


_R = r"R\s*([\d,]+\.\d\d)"
# Juice is paid by the kg and has no Packhouse pack to be matched to.
JUICE_PRODUCT = "LITCHI JUICE, KG"


def parse_onderberg(text: str) -> list:
    """Onderberg's juice payment advice, one per batch of juice loads, as one
    invoice dict per load, or ValueError. Its bookkeeper (rmputter.co.za)
    sends it from the same address as the Boere Unie's membership statements,
    which have no rows like these and are skipped.

    Each load is a row: weighbridge ticket, date, delivered kg, payable kg,
    rotten %, our delivery note, then the rand per payment and the row's
    total. The levies (SALGA's 2% and the rest) are only given for the whole
    advice, so each load carries its share by rand. The agterskot printed
    under it is a promise, not money paid; it isn't filed. The ticket number
    is the invoice number, so a resent advice is skipped load by load."""
    tickets = re.findall(r"\b(\d{5,7})\s+(\d{1,2}-[A-Za-z]{3}-\d{2})\b", text)
    rows = list(re.finditer(r"([\d,]+)\s+(\d+)\s*%\s+(J-?\s*\d+|\d+)\s+" + _R + r"\s+" + _R + r"\s+" + _R, text))
    if not rows:
        raise ValueError("no delivery rows on the payment advice")
    # The printout puts the ticket columns and the money columns side by
    # side; text out of the PDF may give them on separate lines, so they are
    # read apart and paired in order.
    if len(tickets) != len(rows):
        raise ValueError(f"{len(tickets)} weighbridge tickets but {len(rows)} payment rows")
    m = re.search(_R + r"\s+" + _R + r"\s+" + _R, text[rows[-1].end():])
    if not m:
        raise ValueError("no total under the delivery rows")
    gross = _amount(m.group(3))
    levy_text = text.split("DEDUCTIONS", 1)[-1].split("PAYMENT", 1)[0]
    levies = [0.0 if a == "-" else _amount(a) for a in re.findall(r"R\s*(-|[\d,]+\.\d\d)", levy_text)]
    if not levies:
        raise ValueError("no deductions on the payment advice")
    levy = levies[-1]

    out = []
    for (ticket, day), row in zip(tickets, rows):
        part1, part2, total = (_amount(row.group(i)) for i in (4, 5, 6))
        if abs(part1 + part2 - total) > 0.01:
            raise ValueError(f"ticket {ticket}: payments don't add up to {total:.2f}")
        rotten = int(row.group(2))
        out.append({
            "invoice_no": f"OB-{ticket}",
            # Packhouse numbers juice notes on their own, J-0001 on.
            "note_no": f"J-{int(re.sub(r'[^0-9]', '', row.group(3))):04d}",
            "invoice_date": datetime.strptime(day, "%d-%b-%y").date(),
            "payment": f"Juice, {rotten}% rotten" if rotten else "Juice",
            "agent": "Onderberg",
            "lines": [{"product": JUICE_PRODUCT, "sold": int(_amount(row.group(1))), "income": total}],
            "commission": 0.0, "deductions": round(levy * total / gross, 2) if gross else 0.0, "bank_cost": 0.0,
            "gross": total,
        })
    # The checks that stand in for trusting the sender, as on a market invoice.
    if abs(sum(inv["gross"] for inv in out) - gross) > 0.01:
        raise ValueError(f"delivery rows don't add up to the total {gross:.2f}")
    if abs(sum(levies[:-1]) - levy) > 0.01:
        raise ValueError(f"deduction lines don't add up to {levy:.2f}")
    # Rounding the shares can leave a cent or two; the last load takes it.
    out[-1]["deductions"] = round(out[-1]["deductions"] + levy - sum(inv["deductions"] for inv in out), 2)
    for inv in out:
        inv["net"] = round(inv["gross"] - inv["deductions"], 2)
    return out


def save_invoice(inv: dict, session: Session, email_from: str = "", file: str = "") -> bool:
    """Files a parsed invoice. False if that invoice number is already in:
    agents resend, and Andre forwards the odd one twice."""
    if session.get(MarketInvoice, inv["invoice_no"]):
        return False
    session.add(MarketInvoice(
        invoice_no=inv["invoice_no"], note_no=inv["note_no"], invoice_date=inv["invoice_date"],
        payment=inv["payment"], agent=inv["agent"], lines=json.dumps(inv["lines"]),
        gross=inv["gross"], commission=inv["commission"], deductions=inv["deductions"],
        bank_cost=inv["bank_cost"], net=inv["net"], file=file, email_from=email_from,
        imported_at=datetime.utcnow(),
    ))
    session.commit()
    return True


# ---------------------------------------------------------------------------
# Fetching the mail
# ---------------------------------------------------------------------------

def read_status() -> dict:
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"last_run": None, "last_error": "", "log": []}


def _write_status(status: dict) -> None:
    os.makedirs(MAIL_DIR, exist_ok=True)
    status["log"] = status["log"][-50:]
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=1, default=str)


def _handle_message(msg, cfg: dict, session: Session) -> list:
    """What became of each PDF in one email, as log lines."""
    sender = parseaddr(msg.get("From", ""))[1].lower()
    subject = str(msg.get("Subject", ""))[:80]
    domain = sender.rsplit("@", 1)[-1]
    if domain not in cfg["senders"]:
        return [f"Skipped \"{subject}\" from {sender}: not on the senders list"]
    out = []
    for part in msg.walk():
        name = part.get_filename() or ""
        if not name.lower().endswith(".pdf"):
            continue
        data = part.get_payload(decode=True) or b""
        if len(data) > MAX_PDF_BYTES:
            out.append(f"Skipped {name} from {sender}: larger than {MAX_PDF_BYTES // 1024 // 1024} MB")
            continue
        try:
            text = pdf_text(data)
        except Exception as e:
            out.append(f"Skipped {name} from {sender}: not a readable PDF ({e})")
            continue
        if "PRODUCER DAILY SUMMARY" in text:
            out.append(f"Skipped {name}: a daily sales summary, not an invoice")
            continue
        if "PAYMENT ADVICE" in text.upper() and "ONDERBERG" in text.upper():
            try:
                loads = parse_onderberg(text)
            except ValueError as e:
                out.append(f"Refused {name} from {sender}: {e}")
                continue
            os.makedirs(MAIL_DIR, exist_ok=True)
            file = f"onderberg_{loads[0]['invoice_no']}.pdf"
            with open(os.path.join(MAIL_DIR, file), "wb") as f:
                f.write(data)
            for inv in loads:
                if save_invoice(inv, session, sender, file):
                    out.append(f"Imported Onderberg {inv['invoice_no']} for juice note {inv['note_no']}: "
                               f"{inv['lines'][0]['sold']:,} kg, net R{inv['net']:,.2f}")
                else:
                    out.append(f"Onderberg {inv['invoice_no']} (note {inv['note_no']}) was already in")
            continue
        if "TAX INVOICE" not in text or "PRODUCER D/NOTE" not in text:
            out.append(f"Skipped {name} from {sender}: not a market invoice")
            continue
        # Each invoice in a batch stands alone: one that doesn't add up is
        # refused without holding back the others.
        for part in split_invoices(text):
            try:
                inv = parse_invoice(part)
            except ValueError as e:
                out.append(f"Refused {name} from {sender}: {e}")
                continue
            if session.get(MarketInvoice, inv["invoice_no"]):
                out.append(f"Invoice {inv['invoice_no']} (note {inv['note_no']}) was already in")
                continue
            os.makedirs(MAIL_DIR, exist_ok=True)
            file = f"invoice_{inv['invoice_no'].replace('/', '-')}.pdf"
            with open(os.path.join(MAIL_DIR, file), "wb") as f:
                f.write(data)
            save_invoice(inv, session, sender, file)
            out.append(f"Imported invoice {inv['invoice_no']} for note {inv['note_no']} ({inv['payment']}): "
                       f"net R{inv['net']:,.2f}")
    return out or [f"Skipped \"{subject}\" from {sender}: no PDF attached"]


def fetch_once() -> dict:
    """One pass over the inbox. Returns the status the Reports tab shows.
    Runs from the thread and from "Fetch now"; the lock keeps the two from
    reading the same message at once."""
    status = read_status()
    cfg = read_config()
    if not cfg:
        status["last_error"] = "Not set up: data\\market_mail.txt is missing or has no address or app password"
        return status
    with _lock:
        status["last_run"] = datetime.now().isoformat(timespec="seconds")
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=60)
            try:
                imap.login(cfg["address"], cfg["app_password"].replace(" ", ""))
                imap.select("INBOX")
                _, found = imap.uid("SEARCH", "X-GM-RAW", SEARCH)
                for uid in (found[0] or b"").split():
                    _, fetched = imap.uid("FETCH", uid, "(RFC822)")
                    msg = email.message_from_bytes(fetched[0][1])
                    with Session(engine) as session:
                        lines = _handle_message(msg, cfg, session)
                    # Labelled only once handled: a database error above
                    # leaves the message to be tried again next time.
                    imap.uid("STORE", uid, "+X-GM-LABELS", f"({DONE_LABEL})")
                    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                    status["log"] += [f"{stamp}  {line}" for line in lines]
                    for line in lines:
                        print(f"[market mail] {line}", flush=True)
            finally:
                imap.logout()
            status["last_error"] = ""
        except Exception as e:
            # A wrong app password, no internet, Gmail down. Shown on the
            # Reports tab; the next pass tries again.
            status["last_error"] = f"{type(e).__name__}: {e}"
            print(f"[market mail] FAILED: {status['last_error']}", flush=True)
        _write_status(status)
    return status


def _loop() -> None:
    time.sleep(60)  # let the server finish starting first
    while True:
        if read_config():
            try:
                fetch_once()
            except Exception as e:  # never let one bad pass end the thread
                print(f"[market mail] FAILED: {e!r}", flush=True)
        time.sleep(FETCH_MINUTES * 60)


def start_market_mail_scheduler() -> None:
    threading.Thread(target=_loop, daemon=True).start()
