"""Telegram callback_data must stay within the 64-byte API limit."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import auth_users
from handlers.callbacks import shared as cb_shared
from handlers.callbacks import training as cb_training
from handlers import admin_auth

# Worst-case numeric ids seen in production callbacks.
MAX_EXAMPLE_ID = 9223372036854775807
MAX_ESC_ID = 9223372036854775807
MAX_USER_ID = 9223372036854775807

_CALLBACK_RE = re.compile(
    r'callback_data\s*=\s*(?:f"([^"]*\{[^}]*\}[^"]*)"|"([^"]+)")',
)


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _collect_from_markup(markup) -> list[str]:
    if markup is None:
        return []
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


def _collect_from_source(path: Path, *, replacements: dict[str, str]) -> list[str]:
    """Static callback_data literals from source (f-strings expanded with replacements)."""
    text = path.read_text(encoding="utf-8")
    found: list[str] = []
    for m in _CALLBACK_RE.finditer(text):
        raw = m.group(1) or m.group(2) or ""
        expanded = raw
        for key, val in replacements.items():
            expanded = expanded.replace(f"{{{key}}}", val)
        found.append(expanded)
    return found


@pytest.fixture(autouse=True)
def _configure_auth(tmp_path):
    from services import llm_settings

    users_file = tmp_path / "authorized.json"
    llm_file = tmp_path / "llm.json"
    auth_users.configure(
        users_file=str(users_file),
        max_users=5,
        seed_user_ids=[MAX_USER_ID],
        admin_id=MAX_USER_ID,
    )
    llm_settings.configure(settings_file=str(llm_file))


def _callback_buttons_from_builders() -> list[str]:
    callbacks: list[str] = []

    callbacks.extend(_collect_from_markup(auth_users._build_main_menu_keyboard()))
    callbacks.extend(_collect_from_markup(auth_users._build_llm_menu_keyboard()))
    callbacks.extend(_collect_from_markup(auth_users._build_trace_menu_keyboard()))
    callbacks.extend(_collect_from_markup(auth_users._build_user_list_keyboard()))
    callbacks.extend(_collect_from_markup(
        auth_users._build_user_detail_keyboard(MAX_USER_ID),
    ))
    callbacks.extend(_collect_from_markup(
        auth_users._build_pause_duration_keyboard(MAX_USER_ID),
    ))
    callbacks.extend(_collect_from_markup(
        auth_users._build_confirm_delete_keyboard(MAX_USER_ID),
    ))
    callbacks.extend(_collect_from_markup(
        auth_users._build_confirm_clear_notes_keyboard(MAX_USER_ID),
    ))
    callbacks.extend(_collect_from_markup(auth_users._build_back_to_list_keyboard()))
    callbacks.extend(_collect_from_markup(auth_users._build_back_to_menu_keyboard()))

    callbacks.extend(_collect_from_markup(
        cb_shared._build_approval_keyboard(MAX_EXAMPLE_ID, MAX_USER_ID),
    ))

    pending_fp = {
        "verdict": "false_positive",
        "chat_id": MAX_USER_ID,
        "username": "vip",
        "trigger_text": "x",
        "reason": "test",
    }
    pending_open = {"verdict": None}
    callbacks.extend(_collect_from_markup(
        cb_shared._build_escalation_keyboard(MAX_ESC_ID, pending_fp),
    ))
    callbacks.extend(_collect_from_markup(
        cb_shared._build_escalation_keyboard(MAX_ESC_ID, pending_open),
    ))

    training_kb = cb_training.InlineKeyboardMarkup([[
        cb_training.InlineKeyboardButton(
            "Perfecta", callback_data=f"t:good:{MAX_EXAMPLE_ID}",
        ),
        cb_training.InlineKeyboardButton(
            "Corregir", callback_data=f"t:fix:{MAX_EXAMPLE_ID}",
        ),
        cb_training.InlineKeyboardButton(
            "Mala", callback_data=f"t:bad:{MAX_EXAMPLE_ID}",
        ),
    ]])
    callbacks.extend(_collect_from_markup(training_kb))

    repo = Path(__file__).resolve().parents[2]
    for rel in ("handlers/callbacks/shared.py", "handlers/callbacks/training.py"):
        callbacks.extend(_collect_from_source(
            repo / rel,
            replacements={
                "example_id": str(MAX_EXAMPLE_ID),
                "esc_id": str(MAX_ESC_ID),
            },
        ))

    callbacks.extend(_collect_from_source(
        repo / "handlers/admin_auth.py",
        replacements={"uid": str(MAX_USER_ID), "user_id": str(MAX_USER_ID)},
    ))

    return callbacks


def test_all_production_callback_data_within_64_bytes():
    callbacks = _callback_buttons_from_builders()
    assert callbacks, "expected at least one callback_data sample"
    offenders = [
        (cb, _byte_len(cb))
        for cb in callbacks
        if _byte_len(cb) > 64
    ]
    assert not offenders, f"callback_data exceeds 64 bytes: {offenders[:5]}"