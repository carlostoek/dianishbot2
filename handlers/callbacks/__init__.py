"""Callback handlers for approval (a:), training (t:), and escalation (e:) flows."""

from config import DIANA_ADMIN_CHAT_ID
import services.llm as llm_mod
from services.llm import FAIL_ABORTED, failure_label, get_diana_response
from services.delivery import deliver_vip_response
from services.memory import schedule_memory_extract
from services.training import (
    build_escalation_fp_block,
    review_escalation,
    save_example,
    update_bot_response,
    update_rating,
)

from telegram import Update
from telegram.ext import ContextTypes
import auth_users

from .shared import (
    EXPIRED_DRAFT_TEXT,
    MAX_APPROVAL_VARIANTS,
    RegenResult,
    _approval_message_parts,
    _build_approval_keyboard,
    _build_escalation_keyboard,
    _clear_awaiting_note_with_prompt_restore,
    _edit_approval_message,
    _edit_draft_message_expired,
    _format_approval_text,
    _format_escalation_text,
    _refresh_approval_message,
    _refresh_escalation_message,
    _safe_cq_answer,
    _selected_variant,
)
from .approval import (
    _regen_approval_variant,
    _regen_blocked_reason,
    handle_approval_action,
    handle_diana_correction,
    handle_diana_note,
    notify_diana_approval,
)
from .training import (
    handle_training_action,
    notify_diana,
    notify_diana_llm_failure,
)
from .escalation import (
    _generate_from_escalation,
    handle_escalation_action,
    notify_diana_escalation,
)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Maneja callbacks de aprobación (a:), escalaciones (e:) y retroalimentación (t:)."""
    cq = update.callback_query
    if not cq or not cq.data:
        return False

    parts = cq.data.split(":")
    if len(parts) != 3:
        return False

    prefix, action = parts[0], parts[1]
    try:
        ex_id = int(parts[2])
    except ValueError:
        return False
    if prefix not in ("a", "t", "e"):
        return False

    admin_id = auth_users.get_admin_id()
    if admin_id is None or cq.from_user.id != admin_id:
        await cq.answer("No autorizado")
        return True

    if not (
        (prefix == "a" and action in ("approve", "regen", "prev", "next", "fix", "note"))
        or (prefix == "e" and action in ("valid", "fp", "gen"))
    ):
        await cq.answer()

    if prefix == "a":
        await handle_approval_action(cq, context, action, ex_id)
    elif prefix == "e":
        await handle_escalation_action(cq, context, action, ex_id)
    elif prefix == "t":
        await handle_training_action(cq, context, action, ex_id)

    return True


__all__ = [
    "DIANA_ADMIN_CHAT_ID",
    "EXPIRED_DRAFT_TEXT",
    "FAIL_ABORTED",
    "MAX_APPROVAL_VARIANTS",
    "RegenResult",
    "_approval_message_parts",
    "_build_approval_keyboard",
    "_build_escalation_keyboard",
    "_clear_awaiting_note_with_prompt_restore",
    "_edit_approval_message",
    "_edit_draft_message_expired",
    "_format_approval_text",
    "_format_escalation_text",
    "_generate_from_escalation",
    "_refresh_approval_message",
    "_refresh_escalation_message",
    "_regen_approval_variant",
    "_regen_blocked_reason",
    "_safe_cq_answer",
    "_selected_variant",
    "build_escalation_fp_block",
    "deliver_vip_response",
    "failure_label",
    "get_diana_response",
    "handle_callback",
    "handle_diana_correction",
    "handle_diana_note",
    "llm_mod",
    "notify_diana",
    "notify_diana_approval",
    "notify_diana_escalation",
    "notify_diana_llm_failure",
    "review_escalation",
    "save_example",
    "schedule_memory_extract",
    "update_bot_response",
    "update_rating",
]