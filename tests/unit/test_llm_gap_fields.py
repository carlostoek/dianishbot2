"""Unit tests for optional knowledge_gap / gap_question LLM fields (WU1)."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, patch

from state import history
import services.llm as llm_mod
from services.llm import DIANA_RESPONSE_SCHEMA, _parse_gap_fields, _try_parse_llm_json


# ── schema ──────────────────────────────────────────────────────────


def test_schema_includes_optional_gap_fields():
    props = DIANA_RESPONSE_SCHEMA["properties"]
    assert "knowledge_gap" in props
    assert "gap_question" in props
    # required stays response/confidence/topic only
    assert set(DIANA_RESPONSE_SCHEMA["required"]) == {"response", "confidence", "topic"}
    assert DIANA_RESPONSE_SCHEMA.get("additionalProperties") is False


# ── pure parse helpers ──────────────────────────────────────────────


def test_parse_gap_fields_present_true():
    gap, question = _parse_gap_fields({
        "knowledge_gap": True,
        "gap_question": "¿Cómo manejo videollamada privada?",
    })
    assert gap is True
    assert question == "¿Cómo manejo videollamada privada?"


def test_parse_gap_fields_missing_defaults():
    gap, question = _parse_gap_fields({
        "response": "hola",
        "confidence": 80,
        "topic": "saludo",
    })
    assert gap is False
    assert question == ""


def test_parse_gap_fields_nullish_defaults():
    gap, question = _parse_gap_fields({
        "knowledge_gap": None,
        "gap_question": None,
    })
    assert gap is False
    assert question == ""


def test_parse_gap_fields_string_true_coerced():
    gap, question = _parse_gap_fields({
        "knowledge_gap": "true",
        "gap_question": "  need doctrine  ",
    })
    assert gap is True
    assert question == "need doctrine"


def test_parse_gap_fields_falsey():
    gap, question = _parse_gap_fields({
        "knowledge_gap": False,
        "gap_question": "",
    })
    assert gap is False
    assert question == ""


def test_try_parse_keeps_gap_fields():
    raw = json.dumps({
        "response": "mmm no estoy segura",
        "confidence": 60,
        "topic": "limites_contenido",
        "knowledge_gap": True,
        "gap_question": "¿videollamada fuera de tarifa?",
    })
    parsed, fail = _try_parse_llm_json(raw)
    assert fail is None
    assert parsed["knowledge_gap"] is True
    assert "videollamada" in parsed["gap_question"]


# ── get_diana_response 6-tuple ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_history():
    history.clear()
    yield
    history.clear()


@pytest.mark.asyncio
async def test_get_diana_response_returns_6_tuple_with_gap(in_memory_training_db):
    history[11] = [{"role": "user", "content": "videollamada privada?"}]
    payload = json.dumps({
        "response": "eso lo vemos aparte",
        "confidence": 55,
        "topic": "limites_contenido",
        "knowledge_gap": True,
        "gap_question": "¿Cómo debo manejar videollamadas privadas?",
    })
    with patch.object(
        llm_mod, "raw_call", new_callable=AsyncMock, return_value=(payload, None, None),
    ):
        result = await llm_mod.get_diana_response(11, max_retries=1, retry_delay_sec=0)

    assert len(result) == 6
    response, confidence, topic, knowledge_gap, gap_question, failure = result
    assert response == "eso lo vemos aparte"
    assert confidence == 55
    assert topic == "limites_contenido"
    assert knowledge_gap is True
    assert "videollamada" in gap_question
    assert failure is None


@pytest.mark.asyncio
async def test_get_diana_response_missing_gap_defaults(in_memory_training_db):
    history[12] = [{"role": "user", "content": "hola"}]
    payload = json.dumps({
        "response": "hey que onda",
        "confidence": 90,
        "topic": "saludo",
    })
    with patch.object(
        llm_mod, "raw_call", new_callable=AsyncMock, return_value=(payload, None, None),
    ):
        response, confidence, topic, knowledge_gap, gap_question, failure = (
            await llm_mod.get_diana_response(12, max_retries=1, retry_delay_sec=0)
        )

    assert response == "hey que onda"
    assert knowledge_gap is False
    assert gap_question == ""
    assert failure is None


@pytest.mark.asyncio
async def test_get_diana_response_failure_path_6_tuple(in_memory_training_db):
    history[13] = [{"role": "user", "content": "hola"}]
    with patch.object(
        llm_mod, "raw_call", new_callable=AsyncMock,
        return_value=(None, "error_http_api", "HTTP 503"),
    ):
        with patch("services.llm.asyncio.sleep", new_callable=AsyncMock):
            result = await llm_mod.get_diana_response(
                13, max_retries=1, retry_delay_sec=0,
            )

    assert len(result) == 6
    response, confidence, topic, knowledge_gap, gap_question, failure = result
    assert response is None
    assert knowledge_gap is False
    assert gap_question == ""
    assert failure is not None
