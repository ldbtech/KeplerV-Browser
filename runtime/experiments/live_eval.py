"""Run the Guard evaluation with live Meta models and write measured results.

Usage: MODEL_API_KEY=... .venv/bin/python -m runtime.experiments.live_eval [out_dir]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path


async def main(out_dir: str) -> None:
    from runtime.experiments.evaluate import capture_all, evaluate_all, render_markdown, write_report
    from runtime.providers.registry import ProviderRegistry

    if not os.getenv("MODEL_API_KEY"):
        raise SystemExit("MODEL_API_KEY is required for live evaluation.")
    out = Path(out_dir)
    captures = await capture_all(out / "captures")
    failed = {name: cap.checks for name, cap in captures.items() if not cap.passed}
    if failed:
        raise SystemExit(f"Scenario captures failed: {failed}")
    report = await evaluate_all(captures, out / "work", ProviderRegistry(), max_interview_turns=3)
    write_report(report, out)
    print(render_markdown(report))
    print(f"Results written to {out}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else f"runtime/experiments/results/eval-{time.strftime('%Y%m%d-%H%M%S')}"
    asyncio.run(main(target))
