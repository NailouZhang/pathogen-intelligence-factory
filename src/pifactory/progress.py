from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

_START = time.monotonic()


def progress(stage: str, event: str = "info", **fields: Any) -> None:
    """Emit one unbuffered, machine-readable progress line.

    The format is intentionally plain stdout so it appears immediately in
    GitHub Actions and does not depend on the logging configuration.
    """
    payload = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.monotonic() - _START, 1),
        "stage": stage,
        "event": event,
        **fields,
    }
    print("[PIF] " + json.dumps(payload, ensure_ascii=False, default=str), flush=True)
