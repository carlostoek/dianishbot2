"""Topic policies and guidance requests — durable doctrine store.

promo_info-style module-level `db` wire. Schema, CRUD, match, policy block.
Distill / consult flow land in later work units.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from config import GUIDANCE_POLICY_PRIORITY

log = logging.getLogger("diana")

db: sqlite3.Connection | None = None

# Match scoring (design lock)
_TOPIC_MATCH_SCORE = 100
_KEYWORD_HIT_SCORE = 10
_MATCH_TOP_N = 5


def _require_db() -> sqlite3.Connection:
    if db is None:
        raise RuntimeError("knowledge DB not initialized; wire in diana.main()")
    return db


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS topic_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            keywords TEXT,
            policy_summary TEXT NOT NULL,
            example_response TEXT,
            priority INTEGER DEFAULT 100,
            source_question TEXT,
            source_answer_raw TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_topic_policies_topic
          ON topic_policies(topic) WHERE is_active = 1
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guidance_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            username TEXT,
            ts TEXT NOT NULL,
            topic TEXT,
            gap_question TEXT NOT NULL,
            context TEXT,
            draft_response TEXT,
            diana_answer_raw TEXT,
            policy_id INTEGER,
            status TEXT DEFAULT 'pending',
            resolved_at TEXT
        )
    """)


def _now() -> str:
    return datetime.now().isoformat()


def _normalize_topic(topic: str | None) -> str:
    return (topic or "").strip().lower()


def _parse_keywords(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(k) for k in raw]
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(k) for k in data]
    except (TypeError, json.JSONDecodeError):
        pass
    return []


def _row_to_policy(row: sqlite3.Row | tuple) -> dict:
    if not isinstance(row, sqlite3.Row):
        # positional fallback when row_factory unset
        keys = (
            "id", "topic", "keywords", "policy_summary", "example_response",
            "priority", "source_question", "source_answer_raw",
            "created_at", "updated_at", "is_active",
        )
        row = dict(zip(keys, row))
    else:
        row = dict(row)
    row["keywords"] = _parse_keywords(row.get("keywords"))
    return row


def create_policy(
    *,
    topic: str,
    keywords: list[str] | None = None,
    policy_summary: str,
    example_response: str = "",
    priority: int | None = None,
    source_question: str = "",
    source_answer_raw: str = "",
    is_active: int = 1,
) -> int:
    conn = _require_db()
    now = _now()
    pri = GUIDANCE_POLICY_PRIORITY if priority is None else priority
    kw_json = json.dumps(list(keywords or []), ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO topic_policies
           (topic, keywords, policy_summary, example_response, priority,
            source_question, source_answer_raw, created_at, updated_at, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            topic,
            kw_json,
            policy_summary,
            example_response or "",
            pri,
            source_question or "",
            source_answer_raw or "",
            now,
            now,
            is_active,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_policy(policy_id: int) -> dict | None:
    conn = _require_db()
    row = conn.execute(
        """SELECT id, topic, keywords, policy_summary, example_response, priority,
                  source_question, source_answer_raw, created_at, updated_at, is_active
           FROM topic_policies WHERE id = ?""",
        (policy_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_policy(row)


def list_policies(*, include_inactive: bool = False) -> list[dict]:
    conn = _require_db()
    if include_inactive:
        rows = conn.execute(
            """SELECT id, topic, keywords, policy_summary, example_response, priority,
                      source_question, source_answer_raw, created_at, updated_at, is_active
               FROM topic_policies
               ORDER BY priority DESC, id DESC"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, topic, keywords, policy_summary, example_response, priority,
                      source_question, source_answer_raw, created_at, updated_at, is_active
               FROM topic_policies
               WHERE is_active = 1
               ORDER BY priority DESC, id DESC"""
        ).fetchall()
    return [_row_to_policy(r) for r in rows]


def deactivate_policy(policy_id: int) -> bool:
    conn = _require_db()
    cur = conn.execute(
        """UPDATE topic_policies
           SET is_active = 0, updated_at = ?
           WHERE id = ?""",
        (_now(), policy_id),
    )
    conn.commit()
    return cur.rowcount > 0


def create_guidance_request(
    *,
    chat_id: int,
    username: str = "",
    topic: str = "",
    gap_question: str,
    context: list | None = None,
    draft_response: str = "",
) -> int:
    conn = _require_db()
    cur = conn.execute(
        """INSERT INTO guidance_requests
           (chat_id, username, ts, topic, gap_question, context, draft_response, status)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            chat_id,
            username or "",
            _now(),
            topic or "",
            gap_question,
            json.dumps(context or [], ensure_ascii=False),
            draft_response or "",
            "pending",
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_guidance_request(guidance_id: int) -> dict | None:
    conn = _require_db()
    row = conn.execute(
        """SELECT id, chat_id, username, ts, topic, gap_question, context,
                  draft_response, diana_answer_raw, policy_id, status, resolved_at
           FROM guidance_requests WHERE id = ?""",
        (guidance_id,),
    ).fetchone()
    if row is None:
        return None
    if not isinstance(row, sqlite3.Row):
        keys = (
            "id", "chat_id", "username", "ts", "topic", "gap_question", "context",
            "draft_response", "diana_answer_raw", "policy_id", "status", "resolved_at",
        )
        data = dict(zip(keys, row))
    else:
        data = dict(row)
    ctx = data.get("context")
    if isinstance(ctx, str):
        try:
            data["context"] = json.loads(ctx)
        except json.JSONDecodeError:
            data["context"] = []
    elif ctx is None:
        data["context"] = []
    return data


def match_policies(topic: str, *texts: str) -> list[dict]:
    """Score active policies: topic exact +100, each keyword hit +10.

    Floor: topic match (score >= 100) OR at least one keyword hit.
    Ordered by priority DESC, id DESC; capped at top 5.
    """
    conn = _require_db()
    rows = conn.execute(
        """SELECT id, topic, keywords, policy_summary, example_response, priority,
                  source_question, source_answer_raw, created_at, updated_at, is_active
           FROM topic_policies
           WHERE is_active = 1
           ORDER BY priority DESC, id DESC"""
    ).fetchall()

    norm_topic = _normalize_topic(topic)
    combined = " ".join(t or "" for t in texts).lower()

    scored: list[tuple[int, dict]] = []
    for row in rows:
        policy = _row_to_policy(row)
        score = 0
        keyword_hits = 0
        if _normalize_topic(policy["topic"]) == norm_topic and norm_topic:
            score += _TOPIC_MATCH_SCORE
        for kw in policy["keywords"]:
            k = (kw or "").strip().lower()
            if k and k in combined:
                score += _KEYWORD_HIT_SCORE
                keyword_hits += 1
        # Floor: topic match and/or at least one keyword hit
        if score >= _TOPIC_MATCH_SCORE or keyword_hits >= 1:
            scored.append((score, policy))

    # Already ordered by priority/id from SQL; stable re-sort by score then that order
    # Design: "ordered by priority then recency" among eligible — not by score rank.
    # Keep SQL order among eligible (priority DESC, id DESC).
    eligible = [p for _, p in scored]
    return eligible[:_MATCH_TOP_N]


def build_policy_block(policies: list[dict]) -> str:
    """Mandatory instruction block for VIP prompt injection. Empty if no policies."""
    if not policies:
        return ""
    lines = [
        "POLÍTICAS DE DIANA (instrucciones vigentes — síguelas siempre; "
        "tienen prioridad sobre tu criterio genérico; NO las contradigas):",
    ]
    for p in policies:
        topic = p.get("topic") or ""
        summary = p.get("policy_summary") or ""
        example = p.get("example_response") or ""
        lines.append(f"  [{topic}] Regla: {summary}")
        if example:
            lines.append(f'    Ejemplo de tono: "{example}"')
    return "\n" + "\n".join(lines) + "\n"
