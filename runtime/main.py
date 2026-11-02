from __future__ import annotations

import os
import sys

import uvicorn


def main() -> None:
    token = os.getenv("KEPLERV_RUNTIME_TOKEN", "")
    port = os.getenv("KEPLERV_RUNTIME_PORT", "")
    if len(token) < 32 or not port.isdigit():
        raise SystemExit("KeplerV runtime requires an application token and local port.")
    uvicorn.run("runtime.api:app", host="127.0.0.1", port=int(port), log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
