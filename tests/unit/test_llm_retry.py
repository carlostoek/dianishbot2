"""Async tests for LLM retry behavior in get_diana_response."""

import pytest
from unittest.mock import AsyncMock, patch

from state import history
import services.llm as llm_mod


@pytest.fixture(autouse=True)
def _clear_history():
    history.clear()
    yield
    history.clear()


@pytest.mark.asyncio
async def test_retries_on_empty_response_then_succeeds(in_memory_training_db):
    history[42] = [{"role": "user", "content": "hola"}]
    payloads = [
        '{"response": "", "confidence": 80, "topic": "saludo"}',
        '{"response": "hey que onda", "confidence": 85, "topic": "saludo"}',
    ]

    with patch.object(llm_mod, "raw_call", new_callable=AsyncMock) as mock_raw:
        mock_raw.side_effect = [(p, None) for p in payloads]
        with patch("services.llm.asyncio.sleep", new_callable=AsyncMock):
            response, confidence, topic, failure = await llm_mod.get_diana_response(
                42, max_retries=2, retry_delay_sec=0,
            )

    assert response == "hey que onda"
    assert failure is None
    assert confidence == 85
    assert topic == "saludo"
    assert mock_raw.await_count == 2


@pytest.mark.asyncio
async def test_returns_none_after_exhausted_retries(in_memory_training_db):
    history[99] = [{"role": "user", "content": "precio?"}]

    with patch.object(
        llm_mod, "raw_call", new_callable=AsyncMock, return_value=(None, "error_http_api"),
    ) as mock_raw:
        with patch("services.llm.asyncio.sleep", new_callable=AsyncMock):
            response, confidence, topic, failure = await llm_mod.get_diana_response(
                99, max_retries=3, retry_delay_sec=0,
            )

    assert response is None
    assert confidence == 0
    assert topic == "precio"
    assert failure is not None
    assert failure.reason == "error_http_api"
    assert mock_raw.await_count == 3


@pytest.mark.asyncio
async def test_aborts_retry_when_should_abort_returns_true(in_memory_training_db):
    history[7] = [{"role": "user", "content": "hola"}]
    abort_after = {"count": 0}

    def should_abort():
        abort_after["count"] += 1
        return abort_after["count"] > 1

    with patch.object(
        llm_mod, "raw_call", new_callable=AsyncMock, return_value=(None, "error_http_api"),
    ) as mock_raw:
        with patch("services.llm.asyncio.sleep", new_callable=AsyncMock):
            response, confidence, topic, failure = await llm_mod.get_diana_response(
                7,
                max_retries=5,
                retry_delay_sec=0,
                should_abort=should_abort,
            )

    assert response is None
    assert failure is not None
    assert failure.reason == "cancelado_mensaje_nuevo"
    assert mock_raw.await_count == 1


@pytest.mark.asyncio
async def test_retries_on_invalid_json_then_succeeds(in_memory_training_db):
    history[55] = [{"role": "user", "content": "hola"}]
    payloads = [
        "esto no es json",
        '{"response": "listo", "confidence": 90, "topic": "saludo"}',
    ]

    with patch.object(llm_mod, "raw_call", new_callable=AsyncMock) as mock_raw:
        mock_raw.side_effect = [(p, None) for p in payloads]
        with patch("services.llm.asyncio.sleep", new_callable=AsyncMock):
            response, confidence, topic, failure = await llm_mod.get_diana_response(
                55, max_retries=2, retry_delay_sec=0,
            )

    assert response == "listo"
    assert failure is None
    assert confidence == 90
    assert topic == "saludo"
    assert mock_raw.await_count == 2


@pytest.mark.asyncio
async def test_recovers_truncated_json(in_memory_training_db):
    history[88] = [{"role": "user", "content": "hola"}]
    truncated = '{"response": "Holis bien y tu como andas? bonito vie'

    with patch.object(
        llm_mod, "raw_call", new_callable=AsyncMock, return_value=(truncated, None),
    ):
        response, confidence, topic, failure = await llm_mod.get_diana_response(
            88, max_retries=1, retry_delay_sec=0,
        )

    assert failure is None
    assert response == "Holis bien y tu como andas? bonito vie"
    assert confidence == 70