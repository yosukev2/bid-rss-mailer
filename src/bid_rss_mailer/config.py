from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when YAML config is invalid."""


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    organization: str
    url: str
    enabled: bool = True
    timeout_sec: int = 20
    retries: int = 2


@dataclass(frozen=True)
class KeywordSetConfig:
    id: str
    name: str
    enabled: bool
    min_required_matches: int
    required: tuple[str, ...]
    boost: tuple[str, ...]
    exclude: tuple[str, ...]
    exclude_exceptions: tuple[str, ...]
    top_n: int = 10


def _require_str(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _optional_bool(data: dict[str, Any], key: str, default: bool, path: str) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{key} must be bool")
    return value


def _optional_int(data: dict[str, Any], key: str, default: int, minimum: int, path: str) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{path}.{key} must be int >= {minimum}")
    return value


def _require_str_list(data: dict[str, Any], key: str, path: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{path}.{key} must be a non-empty list of strings")
    normalized: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{path}.{key}[{idx}] must be a non-empty string")
        normalized.append(item.strip())
    return tuple(normalized)


def _optional_str_list(data: dict[str, Any], key: str, path: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{path}.{key} must be a list of strings")
    normalized: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{path}.{key}[{idx}] must be a non-empty string")
        normalized.append(item.strip())
    return tuple(normalized)


def _read_yaml(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.exists():
        raise ConfigError(f"Config file does not exist: {resolved}")
    with resolved.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ConfigError(f"Config root must be a mapping: {resolved}")
    return payload


def load_sources_config(path: str | Path) -> list[SourceConfig]:
    payload = _read_yaml(path)
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ConfigError("sources must be a non-empty list")

    seen_ids: set[str] = set()
    parsed: list[SourceConfig] = []
    for index, raw in enumerate(sources):
        node_path = f"sources[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{node_path} must be a mapping")
        source_id = _require_str(raw, "id", node_path)
        if source_id in seen_ids:
            raise ConfigError(f"Duplicate source id: {source_id}")
        seen_ids.add(source_id)
        url = _require_str(raw, "url", node_path)
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ConfigError(f"{node_path}.url must start with http:// or https://")
        parsed.append(
            SourceConfig(
                id=source_id,
                name=_require_str(raw, "name", node_path),
                organization=_require_str(raw, "organization", node_path),
                url=url,
                enabled=_optional_bool(raw, "enabled", True, node_path),
                timeout_sec=_optional_int(raw, "timeout_sec", 20, 1, node_path),
                retries=_optional_int(raw, "retries", 2, 0, node_path),
            )
        )
    return parsed


def load_keyword_sets_config(path: str | Path) -> list[KeywordSetConfig]:
    payload = _read_yaml(path)
    keyword_sets = payload.get("keyword_sets")
    if not isinstance(keyword_sets, list) or not keyword_sets:
        raise ConfigError("keyword_sets must be a non-empty list")

    seen_ids: set[str] = set()
    parsed: list[KeywordSetConfig] = []
    for index, raw in enumerate(keyword_sets):
        node_path = f"keyword_sets[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{node_path} must be a mapping")
        set_id = _require_str(raw, "id", node_path)
        if set_id in seen_ids:
            raise ConfigError(f"Duplicate keyword set id: {set_id}")
        seen_ids.add(set_id)
        min_required_matches = _optional_int(raw, "min_required_matches", 2, 1, node_path)
        parsed.append(
            KeywordSetConfig(
                id=set_id,
                name=_require_str(raw, "name", node_path),
                enabled=_optional_bool(raw, "enabled", True, node_path),
                min_required_matches=min_required_matches,
                required=_require_str_list(raw, "required", node_path),
                boost=_require_str_list(raw, "boost", node_path),
                exclude=_require_str_list(raw, "exclude", node_path),
                exclude_exceptions=_optional_str_list(raw, "exclude_exceptions", node_path),
                top_n=_optional_int(raw, "top_n", 10, 1, node_path),
            )
        )
    return parsed

