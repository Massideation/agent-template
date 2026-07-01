"""email_inbox: two-way email channel for agent-001.

This is the plumbing that lets someone email their agent back with no
login required. It plugs into the existing channel-agnostic pending-message
system in src/inbox.py exactly as another writer/reader of inbox/ and
messages/, with zero changes to decide_next.py's prompt or JSON contract.

check_and_enqueue() polls an IMAP mailbox for new operator messages and
writes them into inbox/<unix-millis>.md (same shape inbox.list_pending_messages()
already reads), plus a inbox/<same-millis>.email.json sidecar carrying the
subject and Message-ID header needed to send a threaded reply later.

deliver_pending_replies() scans messages/ for replies drafted by decide_next
(via inbox.write_reply) that originated from an email inbox message, and
sends them back out over SMTP, threaded as a reply to the original email.

Both functions are defensive: any missing configuration or transport error
is caught and returns a status dict, never raises. Mirrors the style of
src/tasks/respond_to_telegram.py (poll, process defensively, degrade to a
clean skip when unconfigured) and src/emailer.py (env-driven config, a
status dict on every path).

Only Python's stdlib (imaplib, smtplib, email) is used for the mail
transport, so no new third-party dependency is added to the project.
"""

from __future__ import annotations

import email
import imaplib
import json
import re
import smtplib
import time
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

import os


REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = REPO_ROOT / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
MESSAGES_DIR = REPO_ROOT / "messages"

# Defensive cap so one wake never spends unbounded time draining a mailbox.
MAX_MESSAGES_PER_CHECK = 20

_IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "msn.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "ymail.com": "imap.mail.yahoo.com",
}

_SMTP_HOSTS = {
    "gmail.com": "smtp.gmail.com",
    "googlemail.com": "smtp.gmail.com",
    "outlook.com": "smtp.office365.com",
    "hotmail.com": "smtp.office365.com",
    "live.com": "smtp.office365.com",
    "msn.com": "smtp.office365.com",
    "yahoo.com": "smtp.mail.yahoo.com",
    "ymail.com": "smtp.mail.yahoo.com",
}

# Simple heuristic markers for the start of quoted reply history. Matched
# against a whole line, case-insensitive. Deliberately not exhaustive: worst
# case the full quoted thread is left in and the model just sees extra
# context, which is fine.
_QUOTE_MARKERS = re.compile(
    r"^\s*(On .+ wrote:|-----Original Message-----)\s*$",
    re.IGNORECASE,
)


def _get_credentials() -> Optional[tuple[str, str]]:
    """Return (EMAIL_ADDRESS, EMAIL_APP_PASSWORD), or None if either is unset."""
    address = os.environ.get("EMAIL_ADDRESS")
    app_password = os.environ.get("EMAIL_APP_PASSWORD")
    if not address or not app_password:
        return None
    return address, app_password


def _domain_of(address: str) -> str:
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[-1].strip().lower()


def _imap_host(address: str) -> tuple[Optional[str], Optional[str]]:
    """Return (host, error_reason). Explicit EMAIL_IMAP_HOST always wins."""
    explicit = os.environ.get("EMAIL_IMAP_HOST")
    if explicit and explicit.strip():
        return explicit.strip(), None
    host = _IMAP_HOSTS.get(_domain_of(address))
    if host:
        return host, None
    return None, "unknown email provider, set EMAIL_IMAP_HOST explicitly"


def _smtp_host(address: str) -> tuple[Optional[str], Optional[str]]:
    """Return (host, error_reason). Explicit EMAIL_SMTP_HOST always wins."""
    explicit = os.environ.get("EMAIL_SMTP_HOST")
    if explicit and explicit.strip():
        return explicit.strip(), None
    host = _SMTP_HOSTS.get(_domain_of(address))
    if host:
        return host, None
    return None, "unknown email provider, set EMAIL_SMTP_HOST explicitly"


def _strip_quoted_history(body: str) -> str:
    """Best-effort trim of quoted reply history from a plain text body.

    Cuts at the first line that looks like a quoted-reply marker. If no
    marker is found the body is returned unchanged (stripped).
    """
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if _QUOTE_MARKERS.match(line):
            return "\n".join(lines[:i]).strip()
    return body.strip()


def _get_text(part) -> str:
    """Return a MIME part's text content, tolerant of odd charsets."""
    try:
        return part.get_content()
    except Exception:
        try:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return ""


def _extract_plain_text(msg) -> str:
    """Walk a parsed email.message and return the first text/plain part.

    Returns "" if no plain text part is found (for example an HTML-only
    email). Callers treat an empty result as "nothing usable to enqueue".
    """
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            text = _get_text(part)
            if text:
                return text
        return ""
    return _get_text(msg)


def check_and_enqueue() -> dict:
    """Poll the configured mailbox for new operator emails and enqueue them.

    Reads EMAIL_ADDRESS / EMAIL_APP_PASSWORD (required) and optional
    EMAIL_IMAP_HOST from the environment. No-ops cleanly (no connection
    attempt) when unconfigured or when the provider cannot be inferred.
    Never raises: any I/O or transport error is caught and reflected in the
    returned "reason".
    """
    creds = _get_credentials()
    if creds is None:
        return {"checked": False, "reason": "email channel not configured"}
    address, app_password = creds

    host, err = _imap_host(address)
    if err:
        return {"checked": False, "reason": err}

    operator_email = os.environ.get("OPERATOR_EMAIL")
    operator_email_norm = operator_email.strip().lower() if operator_email else None

    notes: list[str] = []
    if not operator_email_norm:
        notes.append(
            "OPERATOR_EMAIL not set: accepting mail from any sender "
            "(permissive fallback, not operator-only)"
        )

    conn = None
    enqueued = 0
    try:
        conn = imaplib.IMAP4_SSL(host)
        conn.login(address, app_password)
        conn.select("INBOX")

        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return {"checked": False, "enqueued": 0, "reason": f"IMAP SEARCH failed: {status}"}

        ids = data[0].split() if data and data[0] else []
        ids = ids[:MAX_MESSAGES_PER_CHECK]

        for msg_id in ids:
            try:
                status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    notes.append(f"fetch failed for id {msg_id!r}: {status}")
                    continue

                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw, policy=policy.default)

                _, sender_addr = parseaddr(str(parsed.get("From", "") or ""))
                sender_addr = sender_addr.strip().lower()

                if operator_email_norm and sender_addr != operator_email_norm:
                    notes.append(
                        f"skipped message from non-operator sender {sender_addr!r}"
                    )
                    continue

                subject = str(parsed.get("Subject", "") or "").strip()
                raw_message_id = parsed.get("Message-ID")
                message_id_header = str(raw_message_id).strip() if raw_message_id else None

                body_raw = _extract_plain_text(parsed)
                body = _strip_quoted_history(body_raw)
                if not body.strip():
                    notes.append(f"skipped empty/unreadable body for id {msg_id!r}")
                    continue

                millis = int(time.time() * 1000)
                INBOX_DIR.mkdir(parents=True, exist_ok=True)
                while (INBOX_DIR / f"{millis}.md").exists():
                    millis += 1

                inbox_path = INBOX_DIR / f"{millis}.md"
                sidecar_path = INBOX_DIR / f"{millis}.email.json"

                inbox_path.write_text(body.strip() + "\n", encoding="utf-8")
                sidecar_payload = {
                    "channel": "email",
                    "subject": subject,
                    "message_id_header": message_id_header,
                    # Not part of the minimal spec'd shape, but needed so
                    # deliver_pending_replies can fall back to replying to
                    # the actual sender when OPERATOR_EMAIL is unset (the
                    # permissive-fallback path above).
                    "from": sender_addr,
                }
                sidecar_path.write_text(
                    json.dumps(sidecar_payload, indent=2) + "\n", encoding="utf-8"
                )

                conn.store(msg_id, "+FLAGS", "\\Seen")
                enqueued += 1
            except Exception as exc:
                notes.append(f"error processing id {msg_id!r}: {type(exc).__name__}")
                continue

    except Exception as exc:
        return {
            "checked": False,
            "enqueued": enqueued,
            "reason": f"IMAP connection failed: {type(exc).__name__}: {exc}",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass

    return {
        "checked": True,
        "enqueued": enqueued,
        "reason": "; ".join(notes) if notes else None,
    }


def _send_smtp(host: str, address: str, app_password: str, msg: EmailMessage) -> None:
    """Send msg via SMTP_SSL on 465, falling back to STARTTLS on 587.

    Handles both common transport styles (Gmail and Yahoo accept implicit
    SSL on 465; Outlook/Office365 requires STARTTLS on 587) without needing
    an extra port env var. Raises the STARTTLS attempt's exception if both
    fail, since that is the more informative of the two for a provider that
    only supports one style.
    """
    try:
        with smtplib.SMTP_SSL(host, 465, timeout=20) as server:
            server.login(address, app_password)
            server.send_message(msg)
        return
    except Exception:
        with smtplib.SMTP(host, 587, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(address, app_password)
            server.send_message(msg)


def _load_email_sidecar(message_id: str) -> Optional[dict]:
    """Return the email sidecar dict for message_id, checking processed first."""
    for candidate in (
        PROCESSED_DIR / f"{message_id}.email.json",
        INBOX_DIR / f"{message_id}.email.json",
    ):
        try:
            if candidate.exists() and candidate.is_file():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def deliver_pending_replies() -> dict:
    """Send any drafted replies that originated from an email inbox message.

    Scans messages/ for <id>-reply.md files without a matching
    <id>-reply.sent marker. Skips any reply id that has no email sidecar
    (it came from a different channel). No-ops cleanly when unconfigured.
    Never raises.
    """
    creds = _get_credentials()
    if creds is None:
        return {"checked": False, "reason": "email channel not configured"}
    address, app_password = creds

    host, err = _smtp_host(address)
    if err:
        return {"checked": False, "reason": err}

    operator_email = os.environ.get("OPERATOR_EMAIL")
    operator_email = operator_email.strip() if operator_email else None

    try:
        reply_paths = sorted(MESSAGES_DIR.glob("*-reply.md")) if MESSAGES_DIR.exists() else []
    except Exception as exc:
        return {"checked": False, "reason": f"could not list messages dir: {type(exc).__name__}"}

    delivered = 0
    notes: list[str] = []

    for reply_path in reply_paths:
        try:
            stem = reply_path.stem
            if not stem.endswith("-reply"):
                continue
            message_id = stem[: -len("-reply")]
            if not message_id or "/" in message_id or ".." in message_id:
                continue

            sent_marker = MESSAGES_DIR / f"{message_id}-reply.sent"
            if sent_marker.exists():
                continue

            sidecar = _load_email_sidecar(message_id)
            if sidecar is None:
                # Not an email-origin message (for example an old web-chat
                # concept reply). Leave it alone; not ours to deliver.
                continue

            try:
                body = reply_path.read_text(encoding="utf-8").strip()
            except Exception as exc:
                notes.append(f"{message_id}: reply body unreadable: {type(exc).__name__}")
                continue
            if not body:
                continue

            subject = str(sidecar.get("subject") or "").strip()
            message_id_header = sidecar.get("message_id_header")
            from_addr = str(sidecar.get("from") or "").strip()

            recipient = operator_email or from_addr
            if not recipient:
                notes.append(f"{message_id}: no recipient available, skipped")
                continue

            reply_subject = f"Re: {subject}" if subject else "Your agent replied"

            msg = EmailMessage()
            msg["From"] = address
            msg["To"] = recipient
            msg["Subject"] = reply_subject
            if message_id_header:
                msg["In-Reply-To"] = message_id_header
                msg["References"] = message_id_header
            msg.set_content(body)

            try:
                _send_smtp(host, address, app_password, msg)
            except Exception as exc:
                notes.append(f"{message_id}: send failed: {type(exc).__name__}")
                continue

            try:
                sent_marker.write_text("", encoding="utf-8")
            except Exception as exc:
                notes.append(f"{message_id}: sent but marker write failed: {type(exc).__name__}")

            delivered += 1
        except Exception as exc:
            notes.append(f"{reply_path.name}: unexpected error: {type(exc).__name__}")
            continue

    return {
        "checked": True,
        "delivered": delivered,
        "reason": "; ".join(notes) if notes else None,
    }
