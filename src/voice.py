"""Optional, free, defensive text-to-speech for agent-001 (Part D).

Off by default. When and only when VOICE_ENABLED == "true" AND a
HUGGINGFACE_TOKEN is present, synthesize a short spoken clip of the agent's
public update via the Hugging Face Inference API using a permissive open TTS
model (Kokoro, Apache-2.0 by default). The clip is written under
logs/public/audio/<date>.<ext> in the agent repo so the wake.yml mirror step
copies it next to the public diary, and the relative path is returned for
persona.json audio_url.

Design rules (carried from docs/PERSONA_PLAN.md Part D):
- Never raise. Any disabled gate, missing token, empty text, network error,
  non-audio body, or write failure returns None silently so the wake is never
  broken.
- $0 to run. Kokoro is Apache-2.0 and commercial-safe; ElevenLabs is avoided
  because its free tier forbids commercial use.
- A 503 "model loading" from Hugging Face is a soft miss: return None this wake
  and try again next wake. No retry storm.
- The model id is a single module-level constant, also overridable via the
  HF_TTS_MODEL environment variable, so it is one-line swappable.

ZERO em dashes in this file's text.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from src import logger


REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = REPO_ROOT / "logs" / "public" / "audio"

# Default open, commercial-safe TTS model (Apache-2.0). Override with the
# HF_TTS_MODEL environment variable if the Inference-API-served id changes.
DEFAULT_HF_TTS_MODEL = "hexgrad/Kokoro-82M"

HF_INFERENCE_BASE = "https://api-inference.huggingface.co/models"

# Cap the spoken text so the clip stays short, the call stays fast, and we stay
# inside the free tier.
MAX_TEXT_CHARS = 300

# Voice ids the Kokoro payload understands. A presentation.voice_id outside this
# set is ignored and the model default voice is used.
SAFE_VOICE_IDS = ["af_heart", "af_bella", "am_adam", "bf_emma"]

# Map a response content-type to a file extension. Defaults to .mp3.
_CONTENT_TYPE_EXT = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
}

_REQUEST_TIMEOUT_SECONDS = 30.0


def _today_local() -> str:
    """Local YYYY-MM-DD, used when a caller omits date_str."""
    return datetime.now().strftime("%Y-%m-%d")


def _enabled() -> bool:
    """True only when voice is explicitly enabled and a token is present."""
    if os.environ.get("VOICE_ENABLED") != "true":
        return False
    if not os.environ.get("HUGGINGFACE_TOKEN"):
        return False
    return True


def _model_id() -> str:
    """Resolve the TTS model id, env override then default."""
    override = (os.environ.get("HF_TTS_MODEL") or "").strip()
    return override or DEFAULT_HF_TTS_MODEL


def _resolve_voice_id(presentation: Optional[Any]) -> Optional[str]:
    """Pull a supported voice id off the presentation, else None.

    Duck-typed: any object exposing a voice_id attribute works, so this module
    does not hard-depend on the Presentation type being defined yet.
    """
    if presentation is None:
        return None
    voice_id = getattr(presentation, "voice_id", None)
    if isinstance(voice_id, str) and voice_id in SAFE_VOICE_IDS:
        return voice_id
    return None


def _ext_for_content_type(content_type: str) -> str:
    """Pick a file extension from a response content-type, default mp3."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(base, "mp3")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to path via a temp file then replace, so a partial clip
    never ships."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def synthesize(
    text: str,
    presentation: Optional[Any] = None,
    date_str: Optional[str] = None,
) -> Optional[str]:
    """Return a repo-relative path to a generated audio clip, or None.

    ``presentation`` and ``date_str`` are optional so a bare
    ``synthesize("hello")`` smoke test (and any future caller) works without
    breaking. When ``date_str`` is omitted, today's local date is used for the
    file name. ``presentation`` defaults to None (model default voice).

    Returns None (silently) when:
      - VOICE_ENABLED is not "true"
      - HUGGINGFACE_TOKEN is missing
      - text is empty or whitespace
      - the HF call fails, times out, returns 503 model-loading, or returns a
        non-audio body
      - writing the file fails

    Never raises. The only success path writes
    logs/public/audio/<date_str>.<ext> and returns
    'logs/public/audio/<date_str>.<ext>' (forward slashes, repo-relative).
    """
    try:
        if not _enabled():
            return None

        clean_text = (text or "").strip()
        if not clean_text:
            return None
        clean_text = clean_text[:MAX_TEXT_CHARS]

        # Default the date only after the gate, so a disabled smoke test stays a
        # no-op and never has to import datetime.
        date_str = (date_str or "").strip() or _today_local()

        token = os.environ.get("HUGGINGFACE_TOKEN") or ""
        model = _model_id()
        url = f"{HF_INFERENCE_BASE}/{model}"

        payload: dict[str, Any] = {"inputs": clean_text}
        voice_id = _resolve_voice_id(presentation)
        if voice_id:
            # Kokoro and several Inference-API TTS models read a voice from the
            # parameters block. An unsupported model simply ignores it.
            payload["parameters"] = {"voice": voice_id}

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "audio/mpeg",
        }

        try:
            resp = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            _log_private(date_str, f"voice request failed: {type(exc).__name__}")
            return None

        # 503 means the model is warming up. Soft miss: try again next wake.
        if resp.status_code == 503:
            _log_private(date_str, "voice: model loading (503), skipping this wake")
            return None

        if resp.status_code != 200:
            _log_private(
                date_str,
                f"voice: HF returned status {resp.status_code}",
            )
            return None

        content_type = resp.headers.get("content-type", "")
        body = resp.content
        if not content_type.lower().startswith("audio/") or not body:
            # A JSON error body (for example a rate-limit or estimated-time
            # notice) lands here. Treat as a soft miss.
            _log_private(
                date_str,
                f"voice: non-audio response (content-type={content_type or 'none'})",
            )
            return None

        ext = _ext_for_content_type(content_type)
        out_path = AUDIO_DIR / f"{date_str}.{ext}"
        _atomic_write_bytes(out_path, body)

        return f"logs/public/audio/{date_str}.{ext}"
    except Exception as exc:
        # Belt and braces: nothing in here is allowed to break the wake.
        _log_private(date_str, f"voice.synthesize swallowed: {type(exc).__name__}")
        return None


def _log_private(date_str: Optional[str], message: str) -> None:
    """Best-effort private note. Never raises."""
    try:
        logger.write_private(date_str or _today_local(), message)
    except Exception:
        pass
