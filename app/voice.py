"""Voice notes: download a WhatsApp audio message (Meta Media API) and turn it into text (OpenAI).

The transcript then goes through the normal text pipeline, so the agent, memory and history don't know the difference.
Audio is held in memory only; neither it nor the transcript is ever logged."""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

MAX_BYTES = 16 * 1024 * 1024  # WhatsApp's own limit for audio
# WhatsApp voice notes are audio/ogg (Opus), which OpenAI accepts as-is. aac / amr are not accepted: they fail softly.
_EXT = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
        "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/wav": "wav", "audio/webm": "webm"}


async def download(media_id: str) -> tuple[bytes, str] | None:
    """(audio bytes, mime type) for a WhatsApp media id, or None if it can't be fetched or is unsuitable."""
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.wa_access_token}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            meta = await client.get(f"https://graph.facebook.com/{s.wa_graph_version}/{media_id}", headers=headers)
            meta.raise_for_status()
            info = meta.json()
            mime = (info.get("mime_type") or "").split(";")[0].strip().lower()
            if mime not in _EXT or int(info.get("file_size") or 0) > MAX_BYTES:
                log.warning("voice note not usable mime=%s size=%s", mime, info.get("file_size"))
                return None
            resp = await client.get(info["url"], headers=headers)  # the download URL needs the token too
            resp.raise_for_status()
            if len(resp.content) > MAX_BYTES:
                return None
            return resp.content, mime
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("voice note download failed: %s", exc)
        return None


async def transcribe(audio: bytes, mime: str) -> str | None:
    """The spoken words in their original language (Chinese, English, mixed), or None when nothing reliable came out."""
    s = get_settings()
    if not s.openai_api_key:
        log.warning("voice note received but OPENAI_API_KEY is not set")
        return None
    import openai

    client = openai.AsyncOpenAI(api_key=s.openai_api_key, timeout=30, max_retries=1)
    try:
        # No language and no prompt: the model detects the language and never translates.
        result = await client.audio.transcriptions.create(
            model=s.transcribe_model, file=(f"voice.{_EXT[mime]}", audio, mime))
    except openai.OpenAIError as exc:
        log.warning("transcription failed: %s", type(exc).__name__)  # never the text or audio
        return None
    text = (result.text or "").strip()
    return text if any(ch.isalnum() for ch in text) else None


async def transcribe_media(media_id: str) -> str | None:
    """Never raises: any failure means "no transcript", so one bad voice note can't block the user's messages."""
    try:
        got = await download(media_id)
        return await transcribe(*got) if got else None
    except Exception as exc:
        log.warning("voice note failed: %s", type(exc).__name__)
        return None
