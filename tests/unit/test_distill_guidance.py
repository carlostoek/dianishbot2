"""Distill guidance answer → topic policy (WU3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services import knowledge
from services.training import get_few_shots


@pytest.fixture
def knowledge_db(test_db):
    old = knowledge.db
    knowledge.db = test_db
    knowledge.init_schema(test_db)
    yield test_db
    knowledge.db = old


@pytest.mark.asyncio
async def test_distill_happy_creates_policy_fields(knowledge_db):
    """Successful LLM distill returns structured fields for a policy row."""
    payload = {
        "topic": "limites_contenido",
        "policy_summary": "No ofrezcas videollamadas privadas fuera de tarifa.",
        "example_response": "eso no lo hago fuera del pack, amor",
        "keywords": ["videollamada", "privado"],
        "priority": 100,
    }

    with patch(
        "services.knowledge.raw_call",
        new_callable=AsyncMock,
        return_value=(json.dumps(payload), None, None),
    ):
        result = await knowledge.distill_guidance(
            gap_question="¿Puedo ofrecer videollamada fuera de tarifa?",
            diana_answer="No hagas videollamadas privadas; redirigí a packs.",
            context=[{"role": "user", "content": "videollamada?"}],
            topic_hint="limites_contenido",
        )

    assert result["topic"] == "limites_contenido"
    assert "videollamada" in result["policy_summary"].lower() or "videollamadas" in result["policy_summary"].lower()
    assert isinstance(result["keywords"], list) and "videollamada" in result["keywords"]
    assert result.get("degraded") is not True
    assert result["example_response"]


@pytest.mark.asyncio
async def test_distill_failure_degrades_to_raw_summary(knowledge_db):
    """On LLM failure, policy_summary = truncated raw answer; still usable."""
    raw = "No ofrezcas X. Redirigí siempre al pack oficial." * 20  # long

    with patch(
        "services.knowledge.raw_call",
        new_callable=AsyncMock,
        return_value=(None, "network", "timeout"),
    ):
        result = await knowledge.distill_guidance(
            gap_question="¿X?",
            diana_answer=raw,
            context=[],
            topic_hint="custom_pack",
        )

    assert result["degraded"] is True
    assert result["topic"] == "custom_pack"
    assert result["policy_summary"]
    assert len(result["policy_summary"]) <= len(raw)
    assert result["policy_summary"].startswith("No ofrezcas")
    # keywords may be empty on degrade
    assert isinstance(result["keywords"], list)


@pytest.mark.asyncio
async def test_distill_invalid_json_degrades(knowledge_db):
    with patch(
        "services.knowledge.raw_call",
        new_callable=AsyncMock,
        return_value=("not-json{{{", None, None),
    ):
        result = await knowledge.distill_guidance(
            gap_question="q",
            diana_answer="Regla simple: nunca hagas Y.",
            context=[],
            topic_hint="general",
        )
    assert result["degraded"] is True
    assert "nunca hagas Y" in result["policy_summary"]


@pytest.mark.asyncio
async def test_distill_does_not_create_few_shot(knowledge_db, in_memory_training_db):
    """First slice: policy only — distill path must not auto-insert few-shot examples."""
    payload = {
        "topic": "limites_contenido",
        "policy_summary": "No VL privadas.",
        "example_response": "no lo hago",
        "keywords": ["videollamada"],
        "priority": 100,
    }
    before = get_few_shots("limites_contenido")

    with patch(
        "services.knowledge.raw_call",
        new_callable=AsyncMock,
        return_value=(json.dumps(payload), None, None),
    ):
        result = await knowledge.distill_guidance(
            gap_question="¿VL?",
            diana_answer="No VL privadas.",
            context=[{"role": "user", "content": "vl?"}],
            topic_hint="limites_contenido",
        )
        # Persist policy as free-text path would
        knowledge.create_policy(
            topic=result["topic"],
            keywords=result["keywords"],
            policy_summary=result["policy_summary"],
            example_response=result.get("example_response") or "",
            source_question="¿VL?",
            source_answer_raw="No VL privadas.",
        )

    after = get_few_shots("limites_contenido")
    assert len(after) == len(before)
