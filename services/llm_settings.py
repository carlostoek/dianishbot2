"""Runtime LLM provider/model settings (hot-reload, persisted to JSON)."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from config import (
    ANTHROPIC_KEY,
    ANTHROPIC_MODEL,
    DEEPSEEK_KEY,
    DEEPSEEK_MODEL,
    LLM_PROVIDER,
    LLM_SETTINGS_FILE,
)

log = logging.getLogger("diana")

_cfg: dict[str, Any] = {}
_state: dict[str, Any] = {}

MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "anthropic": (
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-8",
    ),
}

_PROVIDERS = ("deepseek", "anthropic")

_PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic",
}


def _catalog_default(provider: str) -> str:
    catalog = MODEL_CATALOG.get(provider, ())
    return catalog[0] if catalog else ""


def _clamp_model(provider: str, model: str | None, *, fallback: str | None = None) -> str:
    catalog = MODEL_CATALOG.get(provider, ())
    default = fallback if fallback is not None else _catalog_default(provider)
    if model and model in catalog:
        return model
    if default in catalog:
        return default
    return _catalog_default(provider)


def _default_models() -> dict[str, str]:
    return {
        "deepseek": _clamp_model("deepseek", DEEPSEEK_MODEL, fallback=DEEPSEEK_MODEL),
        "anthropic": _clamp_model("anthropic", ANTHROPIC_MODEL, fallback=ANTHROPIC_MODEL),
    }


DEFAULT_MODELS: dict[str, str] = _default_models()


def configure(**kwargs: Any) -> None:
    global _cfg
    _cfg = kwargs
    _load()


def init() -> None:
    configure(settings_file=LLM_SETTINGS_FILE)


def _settings_path() -> Path:
    return Path(_cfg.get("settings_file", LLM_SETTINGS_FILE))


def _seed_provider() -> str:
    provider = (LLM_PROVIDER or "deepseek").strip().lower()
    if provider not in _PROVIDERS:
        return "deepseek"
    return provider


def _seed_state() -> dict[str, Any]:
    defaults = _default_models()
    return {
        "provider": _seed_provider(),
        "models": {
            "deepseek": defaults["deepseek"],
            "anthropic": defaults["anthropic"],
        },
    }


def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    models = state.get("models") or {}
    return {
        "provider": state["provider"],
        "models": {
            "deepseek": _clamp_model("deepseek", models.get("deepseek")),
            "anthropic": _clamp_model("anthropic", models.get("anthropic")),
        },
    }


def _warn_missing_active_key(provider: str) -> None:
    if not has_api_key(provider):
        log.warning(
            f"Proveedor activo {provider!r} sin API key en .env; "
            "configura la key o cambia proveedor desde el menú admin."
        )


def _validate_and_normalize(data: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Return normalized state and whether migration/save is needed."""
    migrated = False
    defaults = _default_models()

    provider = data.get("provider", _seed_provider())
    if provider not in _PROVIDERS:
        provider = _seed_provider()
        migrated = True

    models_raw = data.get("models")
    if not isinstance(models_raw, dict):
        if "model" in data:
            legacy_model = data.get("model")
            models: dict[str, str] = {
                "deepseek": defaults["deepseek"],
                "anthropic": defaults["anthropic"],
            }
            if isinstance(legacy_model, str):
                clamped = _clamp_model(provider, legacy_model)
                models[provider] = clamped
                if clamped != legacy_model:
                    migrated = True
            migrated = True
        else:
            models = {
                "deepseek": defaults["deepseek"],
                "anthropic": defaults["anthropic"],
            }
            migrated = True
    else:
        models = {
            "deepseek": _clamp_model("deepseek", models_raw.get("deepseek")),
            "anthropic": _clamp_model("anthropic", models_raw.get("anthropic")),
        }
        for p in _PROVIDERS:
            raw_val = models_raw.get(p)
            if raw_val != models[p]:
                migrated = True

    if set(data.keys()) - {"provider", "models", "model"}:
        migrated = True

    return {"provider": provider, "models": models}, migrated


def _load() -> None:
    global _state
    path = _settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                normalized, migrated = _validate_and_normalize(data)
                if normalized:
                    _state = normalized
                    _warn_missing_active_key(_state["provider"])
                    if migrated:
                        _save()
                    return
        except Exception as e:
            log.error(f"Error cargando LLM settings: {e}")

    _state = _seed_state()
    _warn_missing_active_key(_state["provider"])
    _save()


def _save(state: dict[str, Any] | None = None) -> None:
    payload = _serialize_state(state or _state)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def get_provider() -> str:
    return _state.get("provider", _seed_provider())


def get_model() -> str:
    provider = get_provider()
    models = _state.get("models") or {}
    return _clamp_model(provider, models.get(provider))


def has_api_key(provider: str) -> bool:
    if provider == "anthropic":
        return bool(ANTHROPIC_KEY)
    if provider == "deepseek":
        return bool(DEEPSEEK_KEY)
    return False


def set_provider(provider: str) -> tuple[bool, str | None]:
    if provider not in _PROVIDERS:
        return False, "Proveedor inválido"
    if not has_api_key(provider):
        return False, "Falta API key en .env"

    models = dict(_state.get("models") or _default_models())
    models[provider] = _clamp_model(provider, models.get(provider))
    new_state = {"provider": provider, "models": models}

    try:
        _save(new_state)
    except Exception as e:
        log.error(f"Error guardando LLM settings: {e}")
        return False, "Error guardando configuración"

    _state.clear()
    _state.update(new_state)
    return True, None


def set_model(model: str) -> tuple[bool, str | None]:
    provider = get_provider()
    if not has_api_key(provider):
        return False, "Falta API key en .env"
    catalog = MODEL_CATALOG.get(provider, ())
    if model not in catalog:
        return False, "Modelo no permitido"

    models = dict(_state.get("models") or _default_models())
    models[provider] = model
    new_state = {"provider": provider, "models": models}

    try:
        _save(new_state)
    except Exception as e:
        log.error(f"Error guardando LLM settings: {e}")
        return False, "Error guardando configuración"

    _state.clear()
    _state.update(new_state)
    return True, None


def get_display_label() -> str:
    provider = get_provider()
    label = _PROVIDER_LABELS.get(provider, provider)
    return f"{label} / {get_model()}"


def format_estado_llm_line() -> str:
    return f"*LLM:* {get_display_label()}"


def get_active_config() -> tuple[str, str]:
    return get_provider(), get_model()