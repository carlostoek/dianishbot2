"""Tests for LLM failure persistence and reporting."""

from datetime import datetime, timedelta

import pytest

from services.llm import LLMFailure
import services.training as training_mod


@pytest.fixture(autouse=True)
def _db(in_memory_training_db):
    yield


def test_save_and_report_llm_failure(in_memory_training_db):
    failure = LLMFailure("json_invalido", 3, '{"response": "hola')
    training_mod.save_llm_failure(
        1266118954, "Hugh", [{"role": "user", "content": "hola"}], failure, "saludo",
    )
    failure2 = LLMFailure("error_http_api", 3, "503")
    training_mod.save_llm_failure(
        8617981684, "Adan", [{"role": "user", "content": "precio?"}], failure2, "precio",
    )

    stats = training_mod.get_llm_failure_stats(days=7)
    assert stats["total"] == 2
    assert dict(stats["by_reason"]) == {"json_invalido": 1, "error_http_api": 1}
    assert dict(stats["by_user"]) == {"Hugh": 1, "Adan": 1}

    report = training_mod.format_llm_failure_report(days=7)
    assert "Total: 2" in report
    assert "respuesta no es JSON válido" in report
    assert "error HTTP de la API del LLM" in report
    assert "Hugh" in report


def test_report_empty_period(in_memory_training_db):
    report = training_mod.format_llm_failure_report(days=7)
    assert "Sin fallos registrados" in report


def test_stats_respects_day_window(in_memory_training_db):
    conn = in_memory_training_db
    old_ts = (datetime.now() - timedelta(days=10)).isoformat()
    conn.execute(
        """INSERT INTO llm_failures
           (chat_id, username, ts, reason, attempts, detail, topic_guess, context)
           VALUES (?,?,?,?,?,?,?,?)""",
        (1, "old", old_ts, "json_invalido", 3, "", "general", "[]"),
    )
    conn.commit()

    training_mod.save_llm_failure(
        2, "new", [], LLMFailure("error_red", 3, "timeout"), "general",
    )

    stats = training_mod.get_llm_failure_stats(days=7)
    assert stats["total"] == 1