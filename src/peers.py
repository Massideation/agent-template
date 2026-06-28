"""Peer-agent awareness helper.

This module computes a short, sanitized summary of how many other
Free Agent forks exist in the wild. Forkers opt in by adding the
GitHub topic 'free-agent' to their public diary repo.

SAFETY CONTRACT (read carefully before editing):

Peer repos are forked by strangers. Anything they put in their repo name,
description, README, or logs is untrusted text that must never reach the
LLM prompt. This module therefore returns only numeric and date-derived
facts that were computed locally from the GitHub Search API response.
Specifically: total peer count, oldest age in days, newest age in days,
and count active in the past 7 days. Repo names, descriptions, owner
handles, and log contents are NEVER included in the returned string.

On any HTTP error, parse error, or zero matches, the function returns the
empty string and the caller should proceed without peer context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx


SEARCH_URL = (
    "https://api.github.com/search/repositories"
    "?q=topic:free-agent&per_page=100"
)


def _parse_iso(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    # GitHub returns RFC 3339 timestamps ending in 'Z'.
    cleaned = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _age_days(now: datetime, ts: Optional[datetime]) -> Optional[int]:
    if ts is None:
        return None
    delta = now - ts
    days = int(delta.total_seconds() // 86400)
    if days < 0:
        return 0
    return days


def get_peer_summary(timeout: float = 8.0) -> str:
    """Return a short sanitized paragraph about peer agents, or "".

    See module docstring for the safety contract. This function never
    raises; any unexpected error returns the empty string.
    """
    try:
        resp = httpx.get(
            SEARCH_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
    except Exception:
        return ""

    if resp.status_code != 200:
        return ""

    try:
        payload = resp.json()
    except Exception:
        return ""

    if not isinstance(payload, dict):
        return ""

    items = payload.get("items")
    if not isinstance(items, list):
        return ""

    now = datetime.now(timezone.utc)
    ages_created: list[int] = []
    ages_updated: list[int] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        created_age = _age_days(now, _parse_iso(item.get("created_at")))
        if created_age is not None:
            ages_created.append(created_age)
        updated_ts = item.get("pushed_at") or item.get("updated_at")
        updated_age = _age_days(now, _parse_iso(updated_ts))
        if updated_age is not None:
            ages_updated.append(updated_age)

    total = len(ages_created)
    if total == 0:
        return ""

    oldest = max(ages_created)
    newest = min(ages_created)
    active_week = sum(1 for a in ages_updated if a <= 7)

    return (
        f"{total} other Free Agent forks exist. "
        f"Oldest is {oldest} days old. "
        f"Newest is {newest} days old. "
        f"{active_week} were active in the past 7 days."
    )
