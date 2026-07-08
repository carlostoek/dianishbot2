"""Normalize Telegram messages into LLM history text (caption or media placeholder)."""

_MEDIA_PLACEHOLDER = "[{kind}]"


def media_placeholder(kind: str) -> str:
    return _MEDIA_PLACEHOLDER.format(kind=kind)


def _ptb_media_kind(msg) -> str | None:
    if getattr(msg, "photo", None):
        return "foto"
    if getattr(msg, "video", None):
        return "video"
    if getattr(msg, "video_note", None):
        return "video circular"
    if getattr(msg, "voice", None):
        return "nota de voz"
    if getattr(msg, "audio", None):
        return "audio"
    if getattr(msg, "document", None):
        return "documento"
    if getattr(msg, "sticker", None):
        return "sticker"
    if getattr(msg, "animation", None):
        return "gif"
    if getattr(msg, "paid_media", None):
        return "contenido de pago"
    return None


def history_content_from_ptb(msg) -> str:
    """Text, caption, or [media] placeholder for python-telegram-bot Message."""
    text = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()
    if text:
        return text
    kind = _ptb_media_kind(msg)
    if kind:
        return media_placeholder(kind)
    return ""


def telethon_media_kind(msg) -> str | None:
    media = getattr(msg, "media", None)
    if not media:
        return None

    type_name = type(media).__name__
    if type_name == "MessageMediaPhoto":
        return "foto"
    if type_name == "MessageMediaDocument":
        doc = getattr(media, "document", None)
        if doc:
            for attr in getattr(doc, "attributes", ()) or ():
                attr_name = type(attr).__name__
                if attr_name == "DocumentAttributeVideo":
                    if getattr(attr, "round_message", False):
                        return "video circular"
                    return "video"
                if attr_name == "DocumentAttributeAudio":
                    if getattr(attr, "voice", False):
                        return "nota de voz"
                    return "audio"
                if attr_name == "DocumentAttributeSticker":
                    return "sticker"
                if attr_name == "DocumentAttributeAnimated":
                    return "gif"
        return "documento"
    return "multimedia"


def history_content_from_telethon(msg) -> str:
    """Text, caption, or [media] placeholder for a Telethon message object."""
    text = (
        getattr(msg, "text", None)
        or getattr(msg, "message", None)
        or getattr(msg, "caption", None)
        or ""
    ).strip()
    if text:
        return text
    kind = telethon_media_kind(msg)
    if kind:
        return media_placeholder(kind)
    return ""


def history_content_from_record(record: dict) -> str:
    """Text or placeholder from a telethon_import message record dict."""
    text = (record.get("text") or "").strip()
    if text:
        return text
    kind = record.get("media_kind")
    if kind:
        return media_placeholder(kind)
    return ""