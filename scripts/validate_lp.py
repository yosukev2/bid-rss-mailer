from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_PHRASES = (
    "無料枠",
    "有料",
    "免責",
    "有料プランを開始する",
)


def _load(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def _contains(html: str, phrase: str) -> bool:
    return phrase in html


def _extract_checkout_url(config_js: str) -> str:
    matched = re.search(r'checkoutUrl:\s*"([^"]*)"', config_js)
    if not matched:
        return ""
    return matched.group(1).strip()


def _validate_free_payload(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"free payload parse error: {exc}"]
    if not isinstance(payload, dict):
        return ["free payload root must be object"]
    items = payload.get("items")
    if not isinstance(items, list):
        return ["free payload.items must be list"]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"free payload.items[{index}] must be object")
            continue
        for key in ("title", "url", "organization", "date"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"free payload.items[{index}].{key} must be non-empty string")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate LP required fields and structure.")
    parser.add_argument("--index", default="web/lp/index.html")
    parser.add_argument("--config", default="web/lp/config.js")
    parser.add_argument("--free-data", default="web/lp/free_today.json")
    parser.add_argument("--require-checkout-url", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    index_path = Path(args.index)
    config_path = Path(args.config)
    free_path = Path(args.free_data)

    try:
        html = _load(index_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[NG] {exc}")
        return 1
    try:
        config_js = _load(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[NG] {exc}")
        return 1

    for phrase in REQUIRED_PHRASES:
        if not _contains(html, phrase):
            errors.append(f"missing phrase: {phrase}")

    for element_id in ("checkout-link", "free-list", "support-mail"):
        if f'id="{element_id}"' not in html:
            errors.append(f"missing element id: {element_id}")

    checkout_url = _extract_checkout_url(config_js)
    if args.require_checkout_url and not checkout_url:
        errors.append("checkout URL is required but missing")
    if not checkout_url:
        warnings.append("checkout URL is not set (expected before Stripe setup)")

    errors.extend(_validate_free_payload(free_path))

    if warnings:
        for warning in warnings:
            print(f"[WARN] {warning}")
    if errors:
        for error in errors:
            print(f"[NG] {error}")
        return 1

    print("[OK] LP validation passed")
    print(f"[INFO] index={index_path}")
    print(f"[INFO] config={config_path}")
    print(f"[INFO] free_data={free_path}")
    print(f"[INFO] checkout_url_set={'yes' if bool(checkout_url) else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
