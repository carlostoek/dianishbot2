"""Pure logic tests extracted from handlers/business.py (no Telegram)."""

import pytest
from handlers.business import needs_escalation


class TestNeedsEscalation:
    @pytest.mark.parametrize(
        "text,should_match",
        [
            ("cuando es el pago?", "Keyword detectada"),
            ("quiero cancelar mi suscripcion", "Keyword detectada"),
            ("hola todo bien", None),
            ("precio del vip?", "Keyword detectada"),
            ("", None),
        ],
    )
    def test_escalation_keywords(self, text, should_match):
        result = needs_escalation(text)
        if should_match:
            assert result is not None
            assert should_match in result
        else:
            assert result is None
