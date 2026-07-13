"""Policy inject order in get_diana_response (WU3).

Order lock: base → temporal → memory → policies → few_shots → escalation_fp → format
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import state
from services import knowledge
from services import llm as llm_mod


VIP = 888100


@pytest.fixture(autouse=True)
def _reset():
    state.history.clear()
    old_mem = llm_mod.memory_service
    yield
    state.history.clear()
    llm_mod.memory_service = old_mem


def _ok_json():
    return '{"response": "ok", "confidence": 85, "topic": "limites_contenido"}'


@pytest.mark.asyncio
async def test_policy_block_after_memory_before_few_shots(in_memory_training_db):
    """Inject order: memory markers → POLÍTICAS → EJEMPLOS APRENDIDOS."""
    knowledge.create_policy(
        topic="limites_contenido",
        keywords=["videollamada"],
        policy_summary="No ofrezcas videollamada privada fuera de tarifa.",
        example_response="eso no lo hago fuera del pack",
    )
    state.history[VIP] = [
        {"role": "user", "content": "me haces una videollamada privada?"},
    ]

    mock_memory = MagicMock()
    mock_memory.get_context_block.return_value = "\n\n---\nSOBRE ESTE USUARIO: nombre=Test\n"
    llm_mod.memory_service = mock_memory

    few_shot_marker = "EJEMPLOS APRENDIDOS"
    fake_few = (
        f"\n\n---\n{few_shot_marker} (sesiones anteriores — mantén este estilo):\n"
        "• Pregunta similar: hola\n  Respuesta ideal: hey\n---"
    )

    captured: dict = {}

    async def capture_raw(messages, **kwargs):
        captured["messages"] = messages
        return _ok_json(), None, None

    with (
        patch("services.llm.raw_call", new_callable=AsyncMock, side_effect=capture_raw),
        patch("services.training.get_few_shots", return_value=[{"context": [], "response": "x", "correction": ""}]),
        patch("services.training.build_few_shot_block", return_value=fake_few),
    ):
        await llm_mod.get_diana_response(VIP)

    system = captured["messages"][0]["content"]
    mem_idx = system.find("UNTRUSTED USER FACTS")
    pol_idx = system.find("POLÍTICAS DE DIANA")
    few_idx = system.find(few_shot_marker)
    assert mem_idx != -1, "memory block missing"
    assert pol_idx != -1, "policy block missing"
    assert few_idx != -1, "few-shot block missing"
    assert mem_idx < pol_idx < few_idx, (
        f"order wrong: mem={mem_idx}, pol={pol_idx}, few={few_idx}"
    )
    assert "No ofrezcas videollamada" in system


@pytest.mark.asyncio
async def test_empty_match_omits_policy_block(in_memory_training_db):
    """No active match → no POLÍTICAS block in system prompt."""
    state.history[VIP] = [{"role": "user", "content": "hola que tal"}]
    mock_memory = MagicMock()
    mock_memory.get_context_block.return_value = ""
    llm_mod.memory_service = mock_memory

    captured: dict = {}

    async def capture_raw(messages, **kwargs):
        captured["messages"] = messages
        return _ok_json(), None, None

    with patch("services.llm.raw_call", new_callable=AsyncMock, side_effect=capture_raw):
        await llm_mod.get_diana_response(VIP)

    system = captured["messages"][0]["content"]
    assert "POLÍTICAS DE DIANA" not in system


@pytest.mark.asyncio
async def test_inactive_policy_not_injected(in_memory_training_db):
    """Soft-deactivated policies must not appear in the prompt."""
    pid = knowledge.create_policy(
        topic="limites_contenido",
        keywords=["videollamada"],
        policy_summary="REGLA INACTIVA NO DEBE APARECER",
    )
    knowledge.deactivate_policy(pid)
    state.history[VIP] = [
        {"role": "user", "content": "videollamada privada?"},
    ]
    mock_memory = MagicMock()
    mock_memory.get_context_block.return_value = ""
    llm_mod.memory_service = mock_memory

    captured: dict = {}

    async def capture_raw(messages, **kwargs):
        captured["messages"] = messages
        return _ok_json(), None, None

    with patch("services.llm.raw_call", new_callable=AsyncMock, side_effect=capture_raw):
        await llm_mod.get_diana_response(VIP)

    system = captured["messages"][0]["content"]
    assert "REGLA INACTIVA NO DEBE APARECER" not in system
    assert "POLÍTICAS DE DIANA" not in system
