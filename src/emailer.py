"""Send a plain-text email to the agent's operator via Resend.

This is the agent's first hand: a daily digest delivered to the operator's
inbox. It is send-only and free-tier friendly. Using Resend's shared test
sender (onboarding@resend.dev) you can email only the account owner's own
address, which is exactly this use case: the agent emails its own operator.

Defensive: any missing config or transport error returns a dict and never
raises. Mirrors the style of src/web_search.py.

stdlib plus httpx only, keeping the dependency set unchanged.
"""

from __future__ import annotations

import os

import httpx


RESEND_SEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_EMAIL_FROM = "Acme <onboarding@resend.dev>"
TIMEOUT_S = 15.0


def send_operator_email(subject: str, body_text: str) -> dict:
    """Send a plain-text email to the operator via Resend.

    Reads RESEND_API_KEY and OPERATOR_EMAIL from the environment, plus an
    optional EMAIL_FROM (defaults to Resend's shared test sender). If either
    required value is missing, returns {"sent": False, "reason": "email not
    configured"} without sending.

    On HTTP success returns {"sent": True, "id": <id if any>}. On any error
    returns {"sent": False, "reason": <short str>}. This function never raises.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    operator_email = os.environ.get("OPERATOR_EMAIL")
    if not api_key or not operator_email:
        return {"sent": False, "reason": "email not configured"}

    email_from = os.environ.get("EMAIL_FROM") or DEFAULT_EMAIL_FROM

    payload = {
        "from": email_from,
        "to": [operator_email],
        "subject": subject if isinstance(subject, str) else str(subject),
        "text": body_text if isinstance(body_text, str) else str(body_text),
    }

    try:
        response = httpx.post(
            RESEND_SEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"request failed: {type(exc).__name__}"}
    except Exception as exc:
        return {"sent": False, "reason": f"unexpected error: {type(exc).__name__}"}

    if response.status_code not in (200, 201):
        return {
            "sent": False,
            "reason": f"http {response.status_code}",
        }

    message_id = None
    try:
        data = response.json()
        if isinstance(data, dict):
            message_id = data.get("id")
    except Exception:
        message_id = None

    return {"sent": True, "id": message_id}
