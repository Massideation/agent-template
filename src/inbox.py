"""inbox: file-based operator inbox living inside the agent repo.

The admin web UI (in the public diary repo) writes operator messages to
inbox/<unix-millis>.md via the GitHub Contents API. This module is the
agent-side reader. It runs during decide_next:

- list_pending_messages() reads inbox/*.md (skipping inbox/processed/)
  and returns {id, ts, content} dicts so the agent can include them in
  its prompt context.
- write_reply(message_id, reply_text) writes the agent's reply to
  messages/<id>-reply.md, which the admin UI fetches.
- mark_processed(message_id) moves the inbox file into
  inbox/processed/ so the agent does not see it again next wake.

Pure file I/O on the local checkout (which the runner git-clones each
wake). ZERO network. Defensive: any I/O exception is caught and a
warning is printed; never raises out to the wake orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = REPO_ROOT / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
MESSAGES_DIR = REPO_ROOT / "messages"


def list_pending_messages() -> list[dict]:
    """Return pending operator messages, oldest first.

    Each item: {"id": str, "ts": int, "content": str}.
    Skips inbox/processed/. Defensive: returns [] if dir missing or any
    I/O error occurs.
    """
    try:
        if not INBOX_DIR.exists() or not INBOX_DIR.is_dir():
            return []

        items: list[dict] = []
        for path in INBOX_DIR.iterdir():
            try:
                if not path.is_file():
                    continue
                if path.suffix != ".md":
                    continue
                message_id = path.stem
                try:
                    ts = int(message_id)
                except ValueError:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except OSError as exc:
                    print(f"warning: inbox.list_pending_messages read failed for {path.name}: {exc}")
                    continue
                items.append({
                    "id": message_id,
                    "ts": ts,
                    "content": content.strip(),
                })
            except Exception as exc:
                print(f"warning: inbox.list_pending_messages item failed: {exc}")
                continue

        items.sort(key=lambda d: d["ts"])
        return items
    except Exception as exc:
        print(f"warning: inbox.list_pending_messages failed: {exc}")
        return []


def write_reply(message_id: str, reply_text: str) -> Optional[Path]:
    """Write agent's reply to messages/<id>-reply.md.

    Creates messages/ if missing. Defensive: returns None on any error.
    """
    try:
        if not message_id or "/" in message_id or ".." in message_id:
            return None
        text = (reply_text or "").strip()
        if not text:
            return None
        MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
        dst = MESSAGES_DIR / f"{message_id}-reply.md"
        dst.write_text(text + "\n", encoding="utf-8")
        return dst
    except Exception as exc:
        print(f"warning: inbox.write_reply failed for {message_id}: {exc}")
        return None


def mark_processed(message_id: str) -> Optional[Path]:
    """Move inbox/<id>.md into inbox/processed/<id>.md.

    Creates the processed dir if missing. Defensive: returns None on any
    error.
    """
    try:
        if not message_id or "/" in message_id or ".." in message_id:
            return None
        src = INBOX_DIR / f"{message_id}.md"
        if not src.exists() or not src.is_file():
            return None
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        dst = PROCESSED_DIR / f"{message_id}.md"
        src.replace(dst)
        return dst
    except Exception as exc:
        print(f"warning: inbox.mark_processed failed for {message_id}: {exc}")
        return None
