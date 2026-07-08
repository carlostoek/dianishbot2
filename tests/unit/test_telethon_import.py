"""Tests for services/telethon_import.py (no network)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.telethon_import import (
    _message_to_record,
    fetch_all_messages,
    messages_to_history,
    resolve_vip_entity,
)


def test_messages_to_history_maps_diana_as_assistant():
    msgs = [{"text": "hola", "is_diana": True}]
    out = messages_to_history(msgs)
    assert len(out) == 1
    assert out[0] == {"role": "assistant", "content": "hola"}


def test_messages_to_history_skips_empty_text():
    msgs = [
        {"text": "", "is_diana": False},
        {"text": "   ", "is_diana": True},
        {"text": "ok", "is_diana": False},
    ]
    out = messages_to_history(msgs)
    assert len(out) == 1
    assert out[0]["content"] == "ok"


def test_messages_to_history_includes_media_placeholder():
    msgs = [
        {"text": "", "media_kind": "foto", "is_diana": True},
        {"text": "", "media_kind": "video", "is_diana": False},
    ]
    out = messages_to_history(msgs)
    assert out == [
        {"role": "assistant", "content": "[foto]"},
        {"role": "user", "content": "[video]"},
    ]


def test_messages_to_history_chronological_order():
    msgs = [
        {"text": "primero", "is_diana": False},
        {"text": "segundo", "is_diana": True},
        {"text": "tercero", "is_diana": False},
    ]
    out = messages_to_history(msgs)
    assert [m["content"] for m in out] == ["primero", "segundo", "tercero"]


def test_messages_to_history_user_role():
    msgs = [{"text": "hola", "is_diana": False}]
    out = messages_to_history(msgs)
    assert out[0]["role"] == "user"


class _FakeMsg:
    def __init__(self, mid: int, text: str):
        self.id = mid
        self.text = text
        self.out = False
        self.sender_id = 1
        self.date = None
        self.media = None

    async def get_sender(self):
        return None


@pytest.mark.asyncio
async def test_fetch_all_messages_returns_newest_limit_chronological():
    """limit=N must return the N most recent messages, oldest-first."""

    async def iter_messages(entity, limit=None, **kwargs):
        # Telethon default: newest first
        pool = [
            _FakeMsg(30, "newest"),
            _FakeMsg(20, "middle"),
            _FakeMsg(10, "oldest"),
        ]
        for m in pool[:limit]:
            yield m

    client = MagicMock()
    client.get_me = AsyncMock(return_value=MagicMock(id=999))
    client.iter_messages = iter_messages

    out = await fetch_all_messages(client, MagicMock(id=1), limit=2)
    assert [m["text"] for m in out] == ["middle", "newest"]


@pytest.mark.asyncio
async def test_fetch_all_messages_reraises_floodwait_after_max_retries(monkeypatch):
    from telethon.errors import FloodWaitError

    attempts = [0]

    def iter_messages(*args, **kwargs):
        class _RaisingIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                attempts[0] += 1
                raise FloodWaitError(1)

        return _RaisingIter()

    monkeypatch.setattr(
        "services.telethon_import.asyncio.sleep",
        AsyncMock(),
    )

    client = MagicMock()
    client.get_me = AsyncMock(return_value=MagicMock(id=1))
    client.iter_messages = iter_messages

    with pytest.raises(FloodWaitError):
        await fetch_all_messages(client, MagicMock(), limit=10)
    assert attempts[0] == 6


@pytest.mark.asyncio
async def test_resolve_vip_entity_falls_back_to_dialog_scan():
    client = MagicMock()
    client.get_entity = AsyncMock(side_effect=ValueError("cache miss"))
    dialog_entity = MagicMock(id=42, first_name="VIP")

    async def iter_dialogs():
        yield MagicMock(entity=dialog_entity, id=42, name="VIP")

    client.iter_dialogs = iter_dialogs

    entity = await resolve_vip_entity(client, 42, username="vipuser")
    assert entity is dialog_entity


@pytest.mark.asyncio
async def test_message_to_record_logs_debug_when_get_sender_fails(caplog):
    class _BrokenMsg:
        id = 1
        text = "hola"
        out = False
        sender_id = 2
        date = None
        media = None

        async def get_sender(self):
            raise RuntimeError("network down")

    with caplog.at_level("DEBUG", logger="diana"):
        rec = await _message_to_record(_BrokenMsg(), diana_id=999, entity=MagicMock(id=42))

    assert rec["sender_name"] == "Unknown"
    assert any("get_sender falló" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resolve_vip_entity_tries_username_before_dialogs():
    client = MagicMock()
    user_entity = MagicMock(id=42, first_name="VIP")

    async def get_entity(spec):
        if spec == "@vipuser":
            return user_entity
        raise ValueError("cache miss")

    client.get_entity = get_entity
    client.iter_dialogs = MagicMock()

    entity = await resolve_vip_entity(client, 42, username="vipuser")
    assert entity is user_entity
    client.iter_dialogs.assert_not_called()