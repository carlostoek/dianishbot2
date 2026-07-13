"""Admin /politicas and /borrar_politica (WU3)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import auth_users
from services import knowledge
from handlers.admin_auth import handle_admin_message


ADMIN_ID = 555010


@pytest.fixture(autouse=True)
def _set_admin(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(
        users_file=str(users_file), max_users=5, seed_user_ids=[], admin_id=ADMIN_ID,
    )
    auth_users.set_admin_id(ADMIN_ID)
    yield


@pytest.mark.asyncio
async def test_politicas_lists_active(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    knowledge.create_policy(
        topic="limites_contenido",
        keywords=["videollamada"],
        policy_summary="No ofrezcas VL privadas.",
    )
    knowledge.create_policy(
        topic="precio_custom",
        keywords=["pack"],
        policy_summary="No inventes precios.",
    )
    admin = make_user(user_id=ADMIN_ID, username="diana")
    update = make_mock_update(text="/politicas", user=admin)

    handled = await handle_admin_message(update, make_context())

    assert handled is True
    update.message.reply_text.assert_awaited()
    text = update.message.reply_text.await_args[0][0]
    assert "limites_contenido" in text
    assert "precio_custom" in text
    assert "No ofrezcas VL" in text


@pytest.mark.asyncio
async def test_politicas_topic_filter(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    knowledge.create_policy(
        topic="limites_contenido",
        keywords=["vl"],
        policy_summary="Regla A",
    )
    knowledge.create_policy(
        topic="otro_tema",
        keywords=["x"],
        policy_summary="Regla B",
    )
    admin = make_user(user_id=ADMIN_ID, username="diana")
    update = make_mock_update(text="/politicas limites", user=admin)

    await handle_admin_message(update, make_context())
    text = update.message.reply_text.await_args[0][0]
    assert "Regla A" in text
    assert "Regla B" not in text


@pytest.mark.asyncio
async def test_borrar_politica_soft_deactivates(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    pid = knowledge.create_policy(
        topic="limites",
        keywords=["a"],
        policy_summary="Bye",
    )
    admin = make_user(user_id=ADMIN_ID, username="diana")
    update = make_mock_update(text=f"/borrar_politica {pid}", user=admin)

    handled = await handle_admin_message(update, make_context())

    assert handled is True
    row = knowledge.get_policy(pid)
    assert row is not None
    assert row["is_active"] == 0
    text = update.message.reply_text.await_args[0][0]
    assert "desactiv" in text.lower() or "✓" in text
