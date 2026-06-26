"""Unit tests for pure (no I/O) functions in services/llm.py.

These require zero Telegram mocks and are the best starting point.
"""

import pytest
from services.llm import guess_topic, _parse_confidence


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


def test_build_few_shot_block_formats_content():
    exs = [
        {"context": [{"role": "user", "content": "hola"}], "response": "hey", "correction": None, "rating": "good"}
    ]
    block = build_few_shot_block(exs)
    assert "EJEMPLOS APRENDIDOS" in block
    assert "hola" in block
    assert "hey" in block

