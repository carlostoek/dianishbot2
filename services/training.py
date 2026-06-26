import sqlite3
import json
from datetime import datetime, timedelta

from config import DB_FILE, MAX_FEW_SHOTS, SKIP_OBSERVED_TOPICS
from services.llm import failure_label

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            username TEXT,
            ts TEXT NOT NULL,
            reason TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            detail TEXT,
            topic_guess TEXT,
            context TEXT
        )
    """)
    conn.commit()
    return conn


def save_llm_failure(
    chat_id: int,
    username: str,
    context: list[dict],
    failure,
    topic_guess: str,
) -> int:
    conn = _require_db()
    cur = conn.execute(
        """INSERT INTO llm_failures
           (chat_id, username, ts, reason, attempts, detail, topic_guess, context)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            chat_id, username, datetime.now().isoformat(),
            failure.reason, failure.attempts, failure.detail or "",
            topic_guess, json.dumps(context, ensure_ascii=False),
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_llm_failure_stats(days: int = 7) -> dict:
    """Agrega fallos LLM de los últimos N días."""
    conn = _require_db()
    since = (datetime.now() - timedelta(days=days)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM llm_failures WHERE ts >= ?", (since,),
    ).fetchone()[0]

    by_reason = conn.execute(
        """SELECT reason, COUNT(*) FROM llm_failures
           WHERE ts >= ? GROUP BY reason ORDER BY COUNT(*) DESC""",
        (since,),
    ).fetchall()

    by_user = conn.execute(
        """SELECT username, COUNT(*) FROM llm_failures
           WHERE ts >= ? GROUP BY username ORDER BY COUNT(*) DESC""",
        (since,),
    ).fetchall()

    recent = conn.execute(
        """SELECT ts, username, reason, attempts, detail
           FROM llm_failures WHERE ts >= ?
           ORDER BY id DESC LIMIT 8""",
        (since,),
    ).fetchall()

    return {
        "days": days,
        "total": total,
        "by_reason": by_reason,
        "by_user": by_user,
        "recent": recent,
    }


def format_llm_failure_report(days: int = 7) -> str:
    stats = get_llm_failure_stats(days)
    lines = [f"Fallos LLM — últimos {stats['days']} días", "─" * 22]

    if stats["total"] == 0:
        lines.append("Sin fallos registrados en este periodo.")
        return "\n".join(lines)

    lines.append(f"Total: {stats['total']}")
    lines.append("")
    lines.append("Por causa:")
    for reason, count in stats["by_reason"]:
        lines.append(f"  • {failure_label(reason)}: {count}")

    lines.append("")
    lines.append("Por usuario:")
    for username, count in stats["by_user"]:
        lines.append(f"  • {username or '?'}: {count}")

    if stats["recent"]:
        lines.append("")
        lines.append("Últimos:")
        for ts, username, reason, attempts, detail in stats["recent"]:
            ts_short = ts[:16].replace("T", " ")
            lines.append(
                f"  {ts_short} | {username or '?'} | "
                f"{failure_label(reason)} | {attempts} intentos"
            )
            if detail:
                lines.append(f"    {detail[:80]}")

    return "\n".join(lines)


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
    from services.llm import guess_topic  # lazy to avoid static import graph edge in analyzers
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
