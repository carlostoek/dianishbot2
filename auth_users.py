"""Administración de usuarios autorizados — compatibility shim.

Business logic lives in services.auth_service; Telegram handlers in handlers.admin_auth.
"""

from services.auth_service import (
    configure,
    is_authorized,
    get_authorized_ids,
    add_user,
    remove_user,
    set_admin_id,
    get_admin_id,
    mark_history_seeded,
    is_retriable_seed_error,
    get_user_entry,
    get_users_needing_backfill,
    is_history_seeded,
    get_user_count,
    get_max_users,
    all_user_entries,
    is_admin,
    is_auto_send_enabled,
    set_auto_send,
)

from handlers.admin_auth import (
    ESTADO_TITLE,
    handle_callback,
    handle_admin_message,
    handle_admin_note,
    send_user_list,
    send_main_menu,
    send_llm_menu,
    build_estado_text,
    send_estado,
    send_user_detail,
    send_user_notes_view,
    send_confirm_delete,
    send_confirm_clear_notes,
    send_escalaciones,
    _build_main_menu_keyboard,
    _build_llm_menu_keyboard,
    _build_trace_menu_keyboard,
    _build_user_list_keyboard,
    _build_user_detail_keyboard,
    _build_confirm_delete_keyboard,
    _build_confirm_clear_notes_keyboard,
    _build_back_to_list_keyboard,
    _build_back_to_menu_keyboard,
    _start_admin_note_capture,
)