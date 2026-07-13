"""Unit tests for services/knowledge.py — schema, CRUD, match, policy block (WU1)."""

from __future__ import annotations

import json

import pytest

from services import knowledge


@pytest.fixture
def knowledge_db(test_db):
    """Wire knowledge module db + schema for unit tests."""
    old = knowledge.db
    knowledge.db = test_db
    knowledge.init_schema(test_db)
    yield test_db
    knowledge.db = old


# ── schema ──────────────────────────────────────────────────────────


def test_init_schema_creates_topic_policies_table(knowledge_db):
    row = knowledge_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='topic_policies'"
    ).fetchone()
    assert row is not None


def test_init_schema_creates_guidance_requests_table(knowledge_db):
    row = knowledge_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='guidance_requests'"
    ).fetchone()
    assert row is not None


def test_init_schema_topic_policies_columns(knowledge_db):
    cols = {
        r[1]
        for r in knowledge_db.execute("PRAGMA table_info(topic_policies)").fetchall()
    }
    expected = {
        "id",
        "topic",
        "keywords",
        "policy_summary",
        "example_response",
        "priority",
        "source_question",
        "source_answer_raw",
        "created_at",
        "updated_at",
        "is_active",
    }
    assert expected.issubset(cols)


def test_init_schema_guidance_requests_columns(knowledge_db):
    cols = {
        r[1]
        for r in knowledge_db.execute("PRAGMA table_info(guidance_requests)").fetchall()
    }
    expected = {
        "id",
        "chat_id",
        "username",
        "ts",
        "topic",
        "gap_question",
        "context",
        "draft_response",
        "diana_answer_raw",
        "policy_id",
        "status",
        "resolved_at",
    }
    assert expected.issubset(cols)


# ── CRUD ────────────────────────────────────────────────────────────


def test_create_policy_returns_id_and_persists(knowledge_db):
    pid = knowledge.create_policy(
        topic="limites_contenido",
        keywords=["videollamada", "privado"],
        policy_summary="No ofrezcas videollamadas privadas fuera de tarifa.",
        example_response="eso no lo hago fuera del pack, amor",
        priority=100,
        source_question="¿videollamada privada?",
        source_answer_raw="No hago videollamadas privadas.",
    )
    assert isinstance(pid, int) and pid > 0
    row = knowledge.get_policy(pid)
    assert row is not None
    assert row["topic"] == "limites_contenido"
    assert row["keywords"] == ["videollamada", "privado"]
    assert row["policy_summary"].startswith("No ofrezcas")
    assert row["example_response"] == "eso no lo hago fuera del pack, amor"
    assert row["priority"] == 100
    assert row["source_question"] == "¿videollamada privada?"
    assert row["source_answer_raw"] == "No hago videollamadas privadas."
    assert row["is_active"] == 1


def test_create_policy_defaults_active_and_priority(knowledge_db):
    pid = knowledge.create_policy(
        topic="precio_extra",
        keywords=[],
        policy_summary="No inventes descuentos.",
    )
    row = knowledge.get_policy(pid)
    assert row["is_active"] == 1
    assert row["priority"] == 100  # GUIDANCE_POLICY_PRIORITY default
    assert row["keywords"] == []


def test_list_policies_active_only_by_default(knowledge_db):
    a = knowledge.create_policy(
        topic="a", keywords=["x"], policy_summary="A",
    )
    b = knowledge.create_policy(
        topic="b", keywords=["y"], policy_summary="B",
    )
    knowledge.deactivate_policy(b)
    active = knowledge.list_policies()
    ids = {p["id"] for p in active}
    assert a in ids
    assert b not in ids


def test_list_policies_include_inactive(knowledge_db):
    a = knowledge.create_policy(topic="a", keywords=[], policy_summary="A")
    b = knowledge.create_policy(topic="b", keywords=[], policy_summary="B")
    knowledge.deactivate_policy(b)
    all_rows = knowledge.list_policies(include_inactive=True)
    ids = {p["id"] for p in all_rows}
    assert {a, b}.issubset(ids)


def test_deactivate_policy_sets_inactive(knowledge_db):
    pid = knowledge.create_policy(
        topic="t", keywords=["k"], policy_summary="S",
    )
    assert knowledge.deactivate_policy(pid) is True
    row = knowledge.get_policy(pid)
    assert row["is_active"] == 0


def test_create_guidance_request_persists_pending(knowledge_db):
    gid = knowledge.create_guidance_request(
        chat_id=42,
        username="vip",
        topic="limites_contenido",
        gap_question="¿cómo manejo videollamada?",
        context=[{"role": "user", "content": "videollamada?"}],
        draft_response="mmm no se",
    )
    assert isinstance(gid, int) and gid > 0
    row = knowledge.get_guidance_request(gid)
    assert row["status"] == "pending"
    assert row["chat_id"] == 42
    assert row["gap_question"] == "¿cómo manejo videollamada?"
    assert row["draft_response"] == "mmm no se"
    assert row["context"] == [{"role": "user", "content": "videollamada?"}]


# ── match_policies ──────────────────────────────────────────────────


def _seed_policy(
    *,
    topic: str,
    keywords: list[str],
    summary: str = "rule",
    priority: int = 100,
    active: bool = True,
) -> int:
    pid = knowledge.create_policy(
        topic=topic,
        keywords=keywords,
        policy_summary=summary,
        priority=priority,
    )
    if not active:
        knowledge.deactivate_policy(pid)
    return pid


def test_match_exact_topic_scores_eligible(knowledge_db):
    pid = _seed_policy(topic="limites_contenido", keywords=[])
    matches = knowledge.match_policies("limites_contenido", "hola")
    assert len(matches) == 1
    assert matches[0]["id"] == pid


def test_match_topic_is_normalized_case(knowledge_db):
    pid = _seed_policy(topic="Limites_Contenido", keywords=[])
    matches = knowledge.match_policies("limites_contenido", "")
    assert any(m["id"] == pid for m in matches)


def test_match_keyword_hit_without_topic(knowledge_db):
    pid = _seed_policy(
        topic="limites_contenido",
        keywords=["videollamada"],
    )
    matches = knowledge.match_policies(
        "otro_tema",
        "me pediste una videollamada privada?",
    )
    assert any(m["id"] == pid for m in matches)


def test_match_keyword_case_insensitive(knowledge_db):
    pid = _seed_policy(topic="x", keywords=["Videollamada"])
    matches = knowledge.match_policies("y", "pide VIDEO LLAMADA no", "videollamada ya")
    # keyword is substring match on lowercased texts
    matches = knowledge.match_policies("y", "pide videollamada ya")
    assert any(m["id"] == pid for m in matches)


def test_match_inactive_excluded(knowledge_db):
    pid = _seed_policy(
        topic="limites_contenido",
        keywords=["videollamada"],
        active=False,
    )
    matches = knowledge.match_policies(
        "limites_contenido",
        "videollamada por favor",
    )
    assert all(m["id"] != pid for m in matches)
    assert matches == []


def test_match_floor_requires_topic_or_keyword(knowledge_db):
    _seed_policy(topic="alpha", keywords=["zzz"])
    matches = knowledge.match_policies("beta", "nada que ver")
    assert matches == []


def test_match_top_5_cap(knowledge_db):
    for i in range(7):
        _seed_policy(
            topic=f"topic_{i}",
            keywords=["sharedkw"],
            priority=100 + i,
            summary=f"s{i}",
        )
    matches = knowledge.match_policies("other", "sharedkw appears here")
    assert len(matches) == 5


def test_match_orders_by_priority_then_recency(knowledge_db):
    low = _seed_policy(topic="t", keywords=["kw"], priority=10, summary="low")
    high = _seed_policy(topic="t", keywords=["kw"], priority=200, summary="high")
    mid = _seed_policy(topic="t", keywords=["kw"], priority=50, summary="mid")
    matches = knowledge.match_policies("t", "kw")
    ids = [m["id"] for m in matches]
    assert ids[0] == high
    assert low in ids and mid in ids
    # same priority: higher id (more recent) first among remaining
    same_a = _seed_policy(topic="u", keywords=["same"], priority=80, summary="a")
    same_b = _seed_policy(topic="u", keywords=["same"], priority=80, summary="b")
    matches2 = knowledge.match_policies("u", "same")
    assert [m["id"] for m in matches2] == [same_b, same_a]


def test_match_multiple_keywords_score(knowledge_db):
    """Two keyword hits still eligible; single keyword also eligible (floor)."""
    multi = _seed_policy(
        topic="m",
        keywords=["alpha", "beta"],
        priority=50,
        summary="multi",
    )
    single = _seed_policy(
        topic="s",
        keywords=["alpha"],
        priority=50,
        summary="single",
    )
    matches = knowledge.match_policies("z", "alpha and beta together")
    ids = {m["id"] for m in matches}
    assert multi in ids and single in ids


# ── build_policy_block ──────────────────────────────────────────────


def test_build_policy_block_empty_returns_empty():
    assert knowledge.build_policy_block([]) == ""


def test_build_policy_block_labels_as_mandatory_instructions(knowledge_db):
    pid = knowledge.create_policy(
        topic="limites_contenido",
        keywords=["videollamada"],
        policy_summary="No ofrezcas videollamadas privadas.",
        example_response="eso no lo hago fuera del pack",
    )
    row = knowledge.get_policy(pid)
    block = knowledge.build_policy_block([row])
    assert "instrucciones" in block.lower() or "instrucción" in block.lower() or "regla" in block.lower()
    # Must NOT look like optional few-shot style samples alone
    assert "POLÍTICAS" in block or "POLITICAS" in block or "políticas" in block.lower()
    assert "limites_contenido" in block
    assert "No ofrezcas videollamadas privadas." in block
    assert "eso no lo hago fuera del pack" in block
    # Binding language: priority over generic judgment
    low = block.lower()
    assert "prioridad" in low or "síguelas" in low or "siguelas" in low or "no las contradigas" in low


def test_build_policy_block_multiple_policies(knowledge_db):
    p1 = knowledge.create_policy(
        topic="a", keywords=[], policy_summary="Rule A", example_response="ex A",
    )
    p2 = knowledge.create_policy(
        topic="b", keywords=[], policy_summary="Rule B", example_response="ex B",
    )
    rows = [knowledge.get_policy(p1), knowledge.get_policy(p2)]
    block = knowledge.build_policy_block(rows)
    assert "Rule A" in block and "Rule B" in block
