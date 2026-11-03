"""Live benchmark: run real research once and record measured stage latencies.

Usage: MODEL_API_KEY=... .venv/bin/python -m runtime.experiments.benchmark [out_dir]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path


async def run_benchmark(db_path: str | Path, out_dir: str | Path, source_url: str) -> dict:
    from runtime.browser.controller import BrowserController
    from runtime.models import RunRequest
    from runtime.providers.registry import ProviderRegistry
    from runtime.scheduler import RuntimeService
    from runtime.store import RuntimeStore

    started = time.monotonic()
    store = RuntimeStore(db_path)
    service = RuntimeService(store=store, providers=ProviderRegistry(), browser_factory=BrowserController)
    agent = await service.create_run(RunRequest(
        goal_id="goal-benchmark", goal_title="Benchmark research",
        goal_description="Measure one live research run end to end.",
        task="Research the supplied article and report three findings.",
        source_url=source_url,
    ))
    await service._tasks[agent["id"]]
    wall_ms = int((time.monotonic() - started) * 1000)
    trace = service.trace_run(agent["id"])
    benchmark = {
        "source_url": source_url,
        "provider": trace["run"]["provider"],
        "model": trace["run"]["model"],
        "status": trace["run"]["status"],
        "wall_ms": wall_ms,
        "metrics": trace["metrics"],
        "evidence": len(trace["evidence"]),
        "guard_cases": len(trace["guard_cases"]),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    (out / "trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
    return benchmark


async def main(out_dir: str, source_url: str) -> None:
    if not os.getenv("MODEL_API_KEY"):
        raise SystemExit("MODEL_API_KEY is required for the live benchmark.")
    benchmark = await run_benchmark(Path(out_dir) / "benchmark.sqlite3", out_dir, source_url)
    metrics = benchmark["metrics"]
    print(f"status={benchmark['status']} wall={benchmark['wall_ms']}ms model={metrics['model_ms']}ms "
          f"tools={metrics['tool_ms']}ms steps={metrics['steps']} tokens={metrics['tokens_used']} "
          f"denials={metrics['denials']} evidence={benchmark['evidence']}")
    print(f"Benchmark written to {out_dir}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "runtime/experiments/results/benchmark"
    url = sys.argv[2] if len(sys.argv) > 2 else "https://docs.python.org/3/library/asyncio.html"
    asyncio.run(main(target, url))
