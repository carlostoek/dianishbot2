"""Tests for services/message_content.py."""

from unittest.mock import MagicMock

import pytest

from services.message_content import (
    history_content_from_ptb,
    history_content_from_record,
    history_content_from_telethon,
    media_placeholder,
)


class TestMediaPlaceholder:
    def test_formats_kind(self):
        assert media_placeholder("foto") == "[foto]"


class TestHistoryContentFromPtb:
    def test_prefers_text(self):
        msg = MagicMock(text="hola", caption=None, photo=None)
        assert history_content_from_ptb(msg) == "hola"

    def test_uses_caption_when_no_text(self):
        msg = MagicMock(text=None, caption="mira esto", photo=[1])
        assert history_content_from_ptb(msg) == "mira esto"

    def test_photo_without_caption(self):
        msg = MagicMock(
            text=None, caption=None, photo=[1],
            video=None, video_note=None, voice=None, audio=None,
            document=None, sticker=None, animation=None, paid_media=None,
        )
        assert history_content_from_ptb(msg) == "[foto]"

    def test_video_without_caption(self):
        msg = MagicMock(
            text=None, caption=None, photo=None, video=MagicMock(),
            video_note=None, voice=None, audio=None,
            document=None, sticker=None, animation=None, paid_media=None,
        )
        assert history_content_from_ptb(msg) == "[video]"

    def test_voice_without_caption(self):
        msg = MagicMock(
            text=None, caption=None, photo=None, video=None, video_note=None,
            voice=MagicMock(), audio=None, document=None, sticker=None,
            animation=None, paid_media=None,
        )
        assert history_content_from_ptb(msg) == "[nota de voz]"

    def test_audio_without_caption(self):
        msg = MagicMock(
            text=None, caption=None, photo=None, video=None, video_note=None,
            voice=None, audio=MagicMock(), document=None, sticker=None,
            animation=None, paid_media=None,
        )
        assert history_content_from_ptb(msg) == "[audio]"

    def test_empty_non_media(self):
        msg = MagicMock(
            text=None, caption=None, photo=None, video=None, video_note=None,
            voice=None, audio=None, document=None, sticker=None,
            animation=None, paid_media=None,
        )
        assert history_content_from_ptb(msg) == ""


class TestHistoryContentFromRecord:
    def test_text_passthrough(self):
        assert history_content_from_record({"text": "hola"}) == "hola"

    def test_media_kind_placeholder(self):
        rec = {"text": "", "media_kind": "video"}
        assert history_content_from_record(rec) == "[video]"

    def test_skips_empty(self):
        assert history_content_from_record({"text": "", "media_kind": None}) == ""


class TestHistoryContentFromTelethon:
    def test_photo_media(self):
        media = MagicMock()
        type(media).__name__ = "MessageMediaPhoto"
        msg = MagicMock(text="", message=None, caption=None, media=media)
        assert history_content_from_telethon(msg) == "[foto]"

    def test_document_video(self):
        attr = MagicMock()
        type(attr).__name__ = "DocumentAttributeVideo"
        attr.round_message = False
        doc = MagicMock(attributes=[attr])
        media = MagicMock(document=doc)
        type(media).__name__ = "MessageMediaDocument"
        msg = MagicMock(text="", message=None, caption=None, media=media)
        assert history_content_from_telethon(msg) == "[video]"

    def test_document_voice_note(self):
        attr = MagicMock()
        type(attr).__name__ = "DocumentAttributeAudio"
        attr.voice = True
        doc = MagicMock(attributes=[attr])
        media = MagicMock(document=doc)
        type(media).__name__ = "MessageMediaDocument"
        msg = MagicMock(text="", message=None, caption=None, media=media)
        assert history_content_from_telethon(msg) == "[nota de voz]"

    def test_document_audio_file(self):
        attr = MagicMock()
        type(attr).__name__ = "DocumentAttributeAudio"
        attr.voice = False
        doc = MagicMock(attributes=[attr])
        media = MagicMock(document=doc)
        type(media).__name__ = "MessageMediaDocument"
        msg = MagicMock(text="", message=None, caption=None, media=media)
        assert history_content_from_telethon(msg) == "[audio]"