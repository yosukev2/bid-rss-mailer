from __future__ import annotations

import argparse
import os
from pathlib import Path


def build_config_js(checkout_url: str, support_email: str, plan_name: str) -> str:
    escaped_checkout = checkout_url.replace("\\", "\\\\").replace("\"", "\\\"")
    escaped_support = support_email.replace("\\", "\\\\").replace("\"", "\\\"")
    escaped_plan = plan_name.replace("\\", "\\\\").replace("\"", "\\\"")
    return (
        "window.BID_RSS_LP_CONFIG = {\n"
        f"  checkoutUrl: \"{escaped_checkout}\",\n"
        f"  supportEmail: \"{escaped_support}\",\n"
        f"  planName: \"{escaped_plan}\",\n"
        "};\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate web/lp/config.js from environment values.")
    parser.add_argument("--output", default="web/lp/config.js")
    args = parser.parse_args()

    checkout_url = (os.getenv("LP_CHECKOUT_URL") or "").strip()
    support_email = (os.getenv("LP_SUPPORT_EMAIL") or "support@example.com").strip()
    plan_name = (os.getenv("LP_PLAN_NAME") or "月額1,980円").strip()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_config_js(checkout_url=checkout_url, support_email=support_email, plan_name=plan_name),
        encoding="utf-8",
    )
    print(
        "generated lp config: "
        f"path={output} checkout_url_set={'yes' if bool(checkout_url) else 'no'} "
        f"support_email={support_email} plan_name={plan_name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
