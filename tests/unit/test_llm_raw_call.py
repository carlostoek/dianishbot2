"""HTTP-level tests for services.llm.raw_call (aiohttp mocked)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services import llm as llm_mod
from services.llm import raw_call


def _mock_aiohttp_response(*, status: int = 200, json_data=None, text: str = ""):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.json = AsyncMock(return_value=json_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


def _mock_aiohttp_session(post_return):
    session = AsyncMock()
    session.post = MagicMock(return_value=post_return)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_raw_call_deepseek_returns_content_on_200():
    payload = {
        "choices": [{"message": {"content": '  {"response": "hola"}  '}}],
    }
    resp = _mock_aiohttp_response(status=200, json_data=payload)
    session = _mock_aiohttp_session(resp)

    with patch.object(llm_mod, "LLM_PROVIDER", "deepseek"), \
         patch("services.llm.aiohttp.ClientSession", return_value=session):
        content, err = await raw_call([{"role": "user", "content": "hola"}])

    assert content == '{"response": "hola"}'
    assert err is None
    session.post.assert_called_once()


@pytest.mark.asyncio
async def test_raw_call_deepseek_returns_none_on_http_error_status():
    resp = _mock_aiohttp_response(status=503, text="service unavailable")
    session = _mock_aiohttp_session(resp)

    with patch.object(llm_mod, "LLM_PROVIDER", "deepseek"), \
         patch("services.llm.aiohttp.ClientSession", return_value=session):
        content, err = await raw_call([{"role": "user", "content": "hola"}])

    assert content is None
    assert err == "error_http_api"


@pytest.mark.asyncio
async def test_raw_call_deepseek_returns_none_on_network_exception():
    session = AsyncMock()
    session.post = MagicMock(side_effect=TimeoutError("timed out"))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch.object(llm_mod, "LLM_PROVIDER", "deepseek"), \
         patch("services.llm.aiohttp.ClientSession", return_value=session):
        content, err = await raw_call([{"role": "user", "content": "hola"}])

    assert content is None
    assert err == "error_red"


@pytest.mark.asyncio
async def test_raw_call_anthropic_returns_content_on_200():
    payload = {
        "content": [{"type": "text", "text": '{"response": "hola"}'}],
    }
    resp = _mock_aiohttp_response(status=200, json_data=payload)
    session = _mock_aiohttp_session(resp)

    with patch.object(llm_mod, "LLM_PROVIDER", "anthropic"), \
         patch("services.llm.aiohttp.ClientSession", return_value=session):
        content, err = await raw_call(
            [
                {"role": "system", "content": "Eres Diana"},
                {"role": "user", "content": "hola"},
            ],
            response_format={"type": "json_object", "schema": llm_mod.DIANA_RESPONSE_SCHEMA},
        )

    assert content == '{"response": "hola"}'
    assert err is None
    session.post.assert_called_once()
    call_kwargs = session.post.call_args.kwargs
    body = call_kwargs["json"]
    assert body["model"] == llm_mod.ANTHROPIC_MODEL
    assert body["system"] == "Eres Diana"
    assert body["messages"] == [{"role": "user", "content": "hola"}]
    assert body["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_raw_call_anthropic_returns_none_on_http_error_status():
    resp = _mock_aiohttp_response(status=401, text="unauthorized")
    session = _mock_aiohttp_session(resp)

    with patch.object(llm_mod, "LLM_PROVIDER", "anthropic"), \
         patch("services.llm.aiohttp.ClientSession", return_value=session):
        content, err = await raw_call([{"role": "user", "content": "hola"}])

    assert content is None
    assert err == "error_http_api"