#!/usr/bin/env python3
"""One-shot reconciliation of verified business-cycle acceptances back to CatedralX."""
from __future__ import annotations

import json
from api.catedralx_feedback import reconcile_feedback_once


def main() -> int:
    try:
        result = reconcile_feedback_once()
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:400]}))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
