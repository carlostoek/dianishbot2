import sqlite3
import json
from datetime import datetime

from config import DB_FILE, MAX_FEW_SHOTS, SKIP_OBSERVED_TOPICS
from services.llm import guess_topic

import logging
log = logging.getLogger("diana")

db: sqlite3.Connection | None = None
# NOTE (shared conn medium): module-level db shared with MemoryService (same conn
# instance from diana main). Design per PLAN (check_same_thread=False, shared DB,
# no new deps). Low-concurrency ok; aiosqlite + connection pool recommended
# future to prevent "locked" under concurrent main+bg extract tasks.


def _require_db() -> sqlite3.Connection:
    if db is None:
        raise RuntimeError("DB not initialized; call init via main()")
    return db


# ══ SQLITE ══════════════════════════════════════════════
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT,
            ts TEXT,
            context TEXT,
            bot_response TEXT,
            confidence INTEGER,
            topic TEXT,
            rating TEXT,
            correction TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    return conn


def save_example(chat_id, username, context, response, confidence, topic) -> int:
    conn = _require_db()
    cur = conn.execute(
        """INSERT INTO examples
           (chat_id, username, ts, context, bot_response, confidence, topic)
           VALUES (?,?,?,?,?,?,?)""",
        (chat_id, username, datetime.now().isoformat(),
         json.dumps(context, ensure_ascii=False), response, confidence, topic),
    )
    conn.commit()
    return cur.lastrowid


def save_observed_example(
    chat_id: int, username: str, context: list[dict], diana_response: str,
) -> int | None:
    """Guarda un par usuario→Diana observado en chat no autorizado (sin respuesta del bot)."""
    last_user = next(
        (m["content"] for m in reversed(context) if m["role"] == "user"), "",
    )
    if not last_user.strip() or not diana_response.strip():
        return None
    topic = guess_topic(last_user)
    if topic in SKIP_OBSERVED_TOPICS:
        log.info(f"Ejemplo observado omitido — tema '{topic}' excluido del entrenamiento")
        return None
    conn = _require_db()
    cur = conn.execute(
        """INSERT INTO examples
           (chat_id, username, ts, context, bot_response, confidence, topic, rating, status)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (chat_id, username, datetime.now().isoformat(),
         json.dumps(context, ensure_ascii=False), diana_response, 100, topic,
         "diana_manual", "reviewed"),
    )
    conn.commit()
    return cur.lastrowid


def update_rating(example_id: int, rating: str, correction: str | None = None):
    conn = _require_db()
    conn.execute(
        "UPDATE examples SET rating=?, correction=?, status='reviewed' WHERE id=?",
        (rating, correction, example_id),
    )
    conn.commit()


def get_few_shots(topic: str) -> list[dict]:
    """Ejemplos aprobados/corregidos por Diana, ordenados del más reciente al más antiguo."""
    conn = _require_db()
    rows = conn.execute("""
        SELECT context, bot_response, correction, rating
        FROM examples
        WHERE status='reviewed' AND rating IN ('good','corrected','diana_manual') AND topic=?
        ORDER BY id DESC LIMIT ?
    """, (topic, MAX_FEW_SHOTS)).fetchall()
    if not rows:
        rows = conn.execute("""
            SELECT context, bot_response, correction, rating
            FROM examples
            WHERE status='reviewed' AND rating IN ('good','corrected','diana_manual')
            ORDER BY id DESC LIMIT ?
        """, (MAX_FEW_SHOTS,)).fetchall()
    return [
        {"context": json.loads(r[0]), "response": r[1],
         "correction": r[2], "rating": r[3]}
        for r in rows
    ]


def build_few_shot_block(examples: list[dict]) -> str:
    if not examples:
        return ""
    lines = ["\n\n---\nEJEMPLOS APRENDIDOS (sesiones anteriores — mantén este estilo):"]
    for ex in examples:
        last_user = next(
            (m["content"] for m in reversed(ex["context"]) if m["role"] == "user"), "",
        )
        ideal = ex["correction"] or ex["response"]
        lines.append(f"\n• Pregunta similar: {last_user[:120]}")
        lines.append(f"  Respuesta ideal: {ideal}")
        lines.append("---")
    return "\n".join(lines)
