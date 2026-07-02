import logging
import time
from collections import OrderedDict

from telegram import Update
from telegram.ext import Application, ContextTypes
import auth_users
from config import DIANA_ADMIN_CHAT_ID
from state import _load_connections_state
from .business import _handle_business_message
from .recovery import recover_runtime_on_startup
from .callbacks import handle_callback, handle_diana_correction, handle_diana_note
from .admin_menu import handle_admin_input
log = logging.getLogger("diana")

DEDUP_MAX_SIZE = 512
DEDUP_TTL_SEC = 90

_dedup_cache: OrderedDict[str, float] = OrderedDict()


def _is_duplicate_update(update: Update) -> bool:
    now = time.monotonic()
    keys = [f"u:{update.update_id}"]
    if update.callback_query:
        keys.append(f"cq:{update.callback_query.id}")

    duplicate = False
    for key in keys:
        if key in _dedup_cache:
            if now - _dedup_cache[key] < DEDUP_TTL_SEC:
                duplicate = True
            else:
                _dedup_cache[key] = now
                _dedup_cache.move_to_end(key)
        else:
            _dedup_cache[key] = now
            _dedup_cache.move_to_end(key)
            while len(_dedup_cache) > DEDUP_MAX_SIZE:
                _dedup_cache.popitem(last=False)
    return duplicate


async def process_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router principal — despacha según tipo de update."""

    if _is_duplicate_update(update):
        cq_id = update.callback_query.id if update.callback_query else None
        log.debug(
            "duplicate update skipped update_id=%s cq_id=%s",
            update.update_id,
            cq_id,
        )
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                log.debug("duplicate callback answer failed", exc_info=True)
        return

    if update.callback_query:
        if await handle_callback(update, context):
            return
        if await auth_users.handle_callback(update, context):
            return

    if (
        update.message
        and not update.business_message
        and update.message.chat.id == DIANA_ADMIN_CHAT_ID
    ):
        if await auth_users.handle_admin_note(update, context):
            return
        if await handle_diana_note(update, context):
            return
        if await handle_diana_correction(update, context):
            return

    if update.message and not update.business_message:
        admin_id = auth_users.get_admin_id()
        if admin_id and update.message.from_user.id == admin_id:
            # Admin slash commands (/start, /menu, /estado, /ayuda)
            if await handle_admin_input(update, context):
                return
            # Legacy slash commands (/usuarios, /notas, /fallos, etc.)
            if await auth_users.handle_admin_message(update, context):
                return
        elif update.message.from_user:
            sender = update.message.from_user.id
            text = update.message.text or update.message.caption or ""
            log.info(
                f"Mensaje directo al bot ignorado | user:{sender} "
                f"auth:{auth_users.is_authorized(sender)} text:{text[:60]}"
            )
            return

    if update.business_message:
        await _handle_business_message(update.business_message, context)
        return

    if update.edited_business_message:
        await _handle_business_message(
            update.edited_business_message, context, edited=True,
        )
        return


async def _post_init(app: Application) -> None:
    _load_connections_state()
    await recover_runtime_on_startup(app.bot)
    from services import history_backfill
    history_backfill.start_scheduler(app)
