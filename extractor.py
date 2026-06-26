#!/usr/bin/env python3
"""
Diana Chat Extractor

Standalone Telethon tool to pull complete chat histories from Diana's account
and turn them into structured training data for the bot.

SECURITY WARNING (high exposure risk per review Finding 3/6):
ADMIN-ONLY tool for Diana account. Can bulk export ALL private histories + PII/training data + user_memory facts to exports/ and DB.
Use exclusively on trusted/admin systems. Sensitive data exposure risk. No built-in auth or scrubbing.
Prefer offline/airgapped execution. See also memory PII notes.

Usage:
  python extractor.py list
  python extractor.py export --chat 123456789 --format training
  python extractor.py export --chat @username --format training --import-db --limit 500

Requires:
  API_ID and API_HASH in environment or .env (get them at https://my.telegram.org)
  The existing diana_session.session (or it will prompt for login once)
"""

# SECURITY WARNING (high data exposure via extractor):
# This tool can bulk-export full Telegram chat histories + derived facts (from
# memory table / training examples). Contains sensitive PII and private user data.
# Bulk export (--all) and DB import are for ADMIN/DIANA USE ONLY. Do not run
# on untrusted hosts or share exports. No PII redaction applied. Use responsibly.

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import User, Chat, Channel
from telethon.utils import get_display_name

load_dotenv()

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────
SESSION_NAME = "diana_session"
DB_FILE = "diana_training.db"
MAX_CONTEXT_TURNS = 6


def get_api_credentials():
    """Lazy load so --help works without env."""
    api_id = os.getenv("API_ID") or os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("API_HASH") or os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit(
            "Faltan API_ID y API_HASH.\n"
            "Agregalos a tu .env (ejemplo):\n"
            "  API_ID=tu_api_id\n"
            "  API_HASH=tu_api_hash\n"
            "Obtenelos gratis en: https://my.telegram.org"
        )
    return int(api_id), api_hash

EXPORT_DIR = Path("exports")

# Copia ligera del clasificador de diana.py para no depender del bot en runtime
TOPIC_MAP = {
    "precio": ["precio", "costo", "cuánto", "cuanto", "pago", "cobro", "suscripción"],
    "contenido": ["foto", "video", "contenido", "publicación", "pack", "material"],
    "acceso": ["acceso", "link", "canal", "grupo", "entrar", "no puedo"],
    "horarios": ["cuando", "cuándo", "horario", "hora", "disponible", "activa"],
    "presentacion": ["hola", "saludos", "quién eres", "quien eres", "cuéntame"],
}

def guess_topic(text: str) -> str:
    low = (text or "").lower()
    for topic, kws in TOPIC_MAP.items():
        if any(k in low for k in kws):
            return topic
    return "general"


def ensure_export_dir(out_dir: str | None = None) -> Path:
    d = Path(out_dir) if out_dir else EXPORT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_training_examples(messages: list[dict], chat_title: str) -> list[dict]:
    """Construye ejemplos estilo diana_training.db a partir de mensajes ordenados cronológicamente."""
    examples: list[dict] = []
    recent_user: list[str] = []

    for m in messages:
        text = m.get("text", "").strip()
        if not text:
            continue

        if m.get("is_diana"):
            if recent_user:
                ctx = [{"role": "user", "content": u} for u in recent_user[-MAX_CONTEXT_TURNS:]]
                last_user_text = recent_user[-1]
                topic = guess_topic(last_user_text)

                examples.append({
                    "source_chat_id": m.get("chat_id"),
                    "source_msg_id": m.get("id"),
                    "ts": m.get("date"),
                    "username": chat_title,
                    "context": ctx,
                    "bot_response": text,
                    "response": text,
                    "topic": topic,
                    "confidence": 100,
                    "rating": "diana_manual",
                    "status": "reviewed",
                })
            recent_user = []
        else:
            recent_user.append(text)

    return examples


def format_pairs(messages: list[dict]) -> list[dict]:
    """Versión simple de pares (último usuario → respuesta de Diana)."""
    pairs = []
    last_user = None
    for m in messages:
        text = m.get("text", "").strip()
        if not text:
            continue
        if m.get("is_diana"):
            if last_user:
                pairs.append({
                    "user": last_user,
                    "diana": text,
                    "date": m.get("date"),
                })
            last_user = None
        else:
            last_user = text
    return pairs


async def fetch_all_messages(client: TelegramClient, entity, limit: int | None) -> list[dict]:
    """Trae mensajes en orden cronológico (más viejo primero)."""
    out: list[dict] = []
    me = await client.get_me()
    diana_id = me.id
    last_id = 0
    retries = 0
    max_retries = 5
    batch_count = 0
    rate_limit_batch = 200

    while True:
        try:
            async for msg in client.iter_messages(entity, reverse=True, limit=limit, offset_id=last_id):
                if msg is None:
                    continue

                text = (msg.text or getattr(msg, "message", None) or getattr(msg, "caption", None) or "").strip()

                sender_name = "Unknown"
                try:
                    sender = await msg.get_sender()
                    if sender:
                        sender_name = get_display_name(sender)
                except Exception:
                    # Sender puede fallar por permisos/privacidad en algunos mensajes; no es crítico para el export.
                    pass

                out.append({
                    "id": msg.id,
                    "date": msg.date.isoformat() if msg.date else None,
                    "sender_id": msg.sender_id,
                    "sender_name": sender_name,
                    "text": text,
                    "is_diana": bool(msg.out or (msg.sender_id == diana_id)),
                    "has_media": bool(msg.media),
                    "chat_id": getattr(entity, "id", None),
                })
                last_id = msg.id
                batch_count += 1
                if batch_count % rate_limit_batch == 0:
                    await asyncio.sleep(0.5)
            break
        except FloodWaitError as e:
            retries += 1
            if retries > max_retries:
                print(f"  ✗ Demasiados flood waits ({max_retries}). Abortando extracción de este chat.")
                break
            print(f"  ⚠ Flood wait {e.seconds}s — esperando (intento {retries}/{max_retries})...")
            await asyncio.sleep(e.seconds)

    return out


def get_entity_name(entity) -> str:
    if isinstance(entity, (Chat, Channel)):
        return entity.title or str(entity.id)
    if isinstance(entity, User):
        return get_display_name(entity) or str(entity.id)
    return getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(getattr(entity, "id", "unknown"))


async def cmd_list(args: argparse.Namespace) -> None:
    api_id, api_hash = get_api_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    try:
        await client.start()

        print("Chats disponibles (limitados):")
        print("-" * 80)
        count = 0
        async for dialog in client.iter_dialogs(limit=args.limit):
            entity = dialog.entity
            name = dialog.name or get_entity_name(entity)
            uname = getattr(entity, "username", None)
            uname_str = f"@{uname}" if uname else ""
            print(f"{dialog.id:>15}  |  {name[:45]:<45}  {uname_str}")
            count += 1

        print("-" * 80)
        print(f"Total mostrados: {count}")
    finally:
        await client.disconnect()


async def cmd_export(args: argparse.Namespace) -> None:
    out_dir = ensure_export_dir(args.out_dir)
    api_id, api_hash = get_api_credentials()

    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    try:
        await client.start()

        targets = []
        if args.all:
            print("Obteniendo lista de todos los diálogos...")
            async for d in client.iter_dialogs():
                targets.append(d.entity)
        else:
            if not args.chat:
                print("Error: especificá al menos un --chat o usá --all")
                return
            for spec in args.chat:
                spec = spec.strip()
                try:
                    entity = await client.get_entity(spec)
                    targets.append(entity)
                except Exception as e:
                    print(f"No se pudo resolver chat '{spec}': {e}")

        if not targets:
            print("No hay chats para exportar.")
            return

        total_examples = 0
        total_msgs = 0

        for entity in targets:
            chat_id = getattr(entity, "id", None)
            title = get_entity_name(entity)
            safe_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in title)[:30]

            print(f"\n→ Extrayendo: {title} (id={chat_id}) ...")
            msgs = await fetch_all_messages(client, entity, args.limit)
            total_msgs += len(msgs)
            print(f"  Mensajes obtenidos: {len(msgs)}")

            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

            if args.format == "raw":
                payload = {
                    "chat_id": chat_id,
                    "title": title,
                    "exported_at": datetime.utcnow().isoformat(),
                    "message_count": len(msgs),
                    "messages": msgs,
                }
                path = out_dir / f"chat_{chat_id}_{safe_title}_{ts}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                print(f"  Guardado (raw): {path}")

            else:
                if args.format == "training":
                    examples = build_training_examples(msgs, title)
                    records = examples
                    suffix = "training"
                else:
                    records = format_pairs(msgs)
                    suffix = "pairs"

                path = out_dir / f"chat_{chat_id}_{safe_title}_{suffix}_{ts}.jsonl"
                with open(path, "w", encoding="utf-8") as f:
                    for rec in records:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

                print(f"  Guardado ({args.format}): {path} — {len(records)} registros")
                total_examples += len(records)

                if args.import_db and args.format == "training":
                    inserted = import_examples_to_db(records, chat_id, title)
                    print(f"  → Insertados en {DB_FILE}: {inserted} ejemplos (diana_manual)")

        print("\n" + "=" * 50)
        print(f"Listo. Mensajes totales: {total_msgs} | Ejemplos generados: {total_examples}")
    finally:
        await client.disconnect()


def import_examples_to_db(examples: list[dict], chat_id: int, username: str) -> int:
    if not examples:
        return 0

    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cur = conn.cursor()
    inserted = 0

    for ex in examples:
        try:
            ctx_json = json.dumps(ex["context"], ensure_ascii=False)
            ts = ex.get("ts") or datetime.utcnow().isoformat()
            uname = ex.get("username") or username

            cur.execute(
                """
                INSERT INTO examples
                (chat_id, username, ts, context, bot_response, confidence, topic, rating, status)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    chat_id,
                    uname,
                    ts,
                    ctx_json,
                    ex["bot_response"],
                    ex.get("confidence", 100),
                    ex.get("topic", "general"),
                    ex.get("rating", "diana_manual"),
                    ex.get("status", "reviewed"),
                ),
            )
            inserted += 1
        except Exception as e:
            print(f"  Error insertando ejemplo: {e}")

    conn.commit()
    conn.close()
    return inserted


HELP_EPILOG = """
ejemplos:
  python extractor.py list
  python extractor.py list --limit 20
  python extractor.py export --chat 123456789 --format training
  python extractor.py export --chat @username --format training --import-db --limit 500
  python extractor.py export --all -y

requisitos:
  API_ID y API_HASH en .env — https://my.telegram.org
  Sesión diana_session.session (login interactivo la primera vez)
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extractor.py",
        description="Extractor de chats de Telegram para entrenamiento de Diana",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="comando")

    # list
    p_list = subparsers.add_parser("list", help="Lista los chats/diálogos disponibles")
    p_list.add_argument("--limit", type=int, default=80, help="Cuántos diálogos mostrar (default 80)")

    # export
    p_exp = subparsers.add_parser("export", help="Exporta historial completo de uno o más chats")
    p_exp.add_argument(
        "--chat", "-c", action="append",
        help="ID numérico o @username. Repetir para varios chats. Ej: -c 123 -c @vic"
    )
    p_exp.add_argument("--all", action="store_true", help="Exportar TODOS los chats (cuidado)")
    p_exp.add_argument(
        "--format", "-f",
        choices=["raw", "training", "pairs"],
        default="training",
        help="raw = JSON completo | training = listo para DB | pairs = user/diana simples"
    )
    p_exp.add_argument("--out-dir", default="exports", help="Directorio de salida")
    p_exp.add_argument("--limit", type=int, default=None, help="Limitar cantidad de mensajes (útil para pruebas)")
    p_exp.add_argument("--import-db", action="store_true", help="Importar automáticamente los ejemplos de training al diana_training.db")
    p_exp.add_argument("-y", "--yes", action="store_true", help="No pedir confirmación")

    return parser


async def async_main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "list":
        await cmd_list(args)
    elif args.command == "export":
        if args.all and not args.yes:
            print("⚠️  Vas a exportar TODOS los chats. Esto puede tardar y generar mucho volumen.")
            resp = input("¿Continuar? [y/N]: ").strip().lower()
            if resp != "y":
                print("Cancelado.")
                return
        await cmd_export(args)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
