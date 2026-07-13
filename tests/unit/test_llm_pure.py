"""Unit tests for pure (no I/O) functions in services/llm.py.

These require zero Telegram mocks and are the best starting point.
"""

import pytest
from services.llm import guess_topic, _parse_confidence, _try_parse_llm_json


class TestGuessTopic:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("cuánto cuesta el pack?", "precio"),
            ("quiero ver fotos y videos", "contenido"),
            ("no puedo entrar al link", "acceso"),
            ("a que hora estas activa?", "horarios"),
            ("hola, quien eres?", "presentacion"),
            ("cuando subes nuevo material", "contenido"),
            ("random question about life", "general"),
        ],
    )
    def test_keywords_map_to_topics(self, text, expected):
        assert guess_topic(text) == expected

    def test_empty_and_none_are_general(self):
        assert guess_topic("") == "general"
        assert guess_topic(None) == "general"


class TestParseConfidence:
    def test_valid_int(self):
        assert _parse_confidence(87) == 87
        assert _parse_confidence("92") == 92

    def test_invalid_falls_back_to_100(self):
        assert _parse_confidence("foo") == 100
        assert _parse_confidence(None) == 100
        assert _parse_confidence([]) == 100


from services.training import build_few_shot_block


def test_build_few_shot_block_empty():
    assert build_few_shot_block([]) == ""


class TestTryParseLlmJson:
    def test_parses_valid_json(self):
        parsed, fail = _try_parse_llm_json(
            '{"response": "hola", "confidence": 85, "topic": "saludo"}',
        )
        assert fail is None
        assert parsed["response"] == "hola"

    def test_parses_optional_gap_fields_when_present(self):
        parsed, fail = _try_parse_llm_json(
            '{"response": "x", "confidence": 50, "topic": "t",'
            ' "knowledge_gap": true, "gap_question": "how?"}',
        )
        assert fail is None
        assert parsed["knowledge_gap"] is True
        assert parsed["gap_question"] == "how?"

    def test_strips_deepseek_thinking_suffix(self):
        raw = (
            '{"response": "holis 😊 qué haces? jsjs", "confidence": 85, "topic": "saludo"}'
            "<｜end▁of▁thinking｜>holis 😊 qué haces? jsjs"
        )
        parsed, fail = _try_parse_llm_json(raw)
        assert fail is None
        assert parsed["response"] == "holis 😊 qué haces? jsjs"
        assert parsed["confidence"] == 85
        assert parsed["topic"] == "saludo"

    def test_recovers_truncated_json_without_tail_garbage(self):
        truncated = '{"response": "Holis bien y tu como andas? bonito vie'
        parsed, fail = _try_parse_llm_json(truncated)
        assert fail is None
        assert parsed["response"] == "Holis bien y tu como andas? bonito vie"
        assert parsed["confidence"] == 70


def test_build_few_shot_block_formats_content():
    exs = [
        {"context": [{"role": "user", "content": "hola"}], "response": "hey", "correction": None, "rating": "good"}
    ]
    block = build_few_shot_block(exs)
    assert "EJEMPLOS APRENDIDOS" in block
    assert "hola" in block
    assert "hey" in block

