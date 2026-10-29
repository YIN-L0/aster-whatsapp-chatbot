"""Compare LLM configurations on speed and behaviour using the scripted scenarios (scripts/run_scenarios.py).

    .venv/bin/python -m scripts.benchmark_models                         # all configs, 2 runs each
    .venv/bin/python -m scripts.benchmark_models gpt5-minimal opus-low --runs 3

Each run is a separate process with its own throwaway DB; runs go in parallel. Bookings go to the demo mock, never
to the real booking backend. Costs real tokens – roughly 30 patient turns per run.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

CONFIGS = {
    "gpt5-low": {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5", "OPENAI_REASONING_EFFORT": "low"},
    "gpt5-minimal": {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5", "OPENAI_REASONING_EFFORT": "minimal"},
    "gpt5mini-minimal": {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5-mini", "OPENAI_REASONING_EFFORT": "minimal"},
    "gpt4.1": {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4.1", "OPENAI_REASONING_EFFORT": ""},
    "gpt4.1-mini": {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4.1-mini", "OPENAI_REASONING_EFFORT": ""},
    "gpt4o": {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4o", "OPENAI_REASONING_EFFORT": ""},
    "opus-low": {"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "claude-opus-5-5", "ANTHROPIC_EFFORT": "low",
                 "ANTHROPIC_SPEED": ""},
    "opus-low-fast": {"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "claude-opus-5-5", "ANTHROPIC_EFFORT": "low",
                      "ANTHROPIC_SPEED": "fast"},
    "sonnet-low": {"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "claude-sonnet-5-5", "ANTHROPIC_EFFORT": "low",
                   "ANTHROPIC_SPEED": ""},
    "haiku-low": {"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "claude-haiku-5-5", "ANTHROPIC_EFFORT": "low",
                 "ANTHROPIC_SPEED": ""},
    "haiku4.5": {"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "claude-haiku-4-5", "ANTHROPIC_EFFORT": "",
                 "ANTHROPIC_SPEED": ""},
}


async def run_one(name: str, i: int, out_dir: Path) -> Path:
    json_path = out_dir / f"{name}-{i}.json"
    env = {**os.environ, **CONFIGS[name], "BOOKING_API_URL": ""}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "scripts.run_scenarios", "--json", str(json_path), env=env,
        stdout=open(out_dir / f"{name}-{i}.log", "w"), stderr=asyncio.subprocess.STDOUT,
    )
    await proc.wait()
    return json_path


def summarize(name: str, paths: list[Path]) -> dict | None:
    runs = [json.loads(p.read_text()) for p in paths if p.exists()]
    if not runs:
        return None
    turns = [t for r in runs for t in r]
    secs = sorted(t["seconds"] for t in turns)
    fails = Counter(f["category"] for t in turns for f in t["failures"])
    return {
        "config": name, "runs": len(runs), "turns": len(turns),
        "avg": sum(secs) / len(secs), "median": secs[len(secs) // 2], "p90": secs[int(len(secs) * 0.9)],
        "max": secs[-1], "steps": sum(t["llm_steps"] for t in turns) / len(turns),
        "tokens": sum(t["tokens"] for t in turns) / len(turns),
        **{c: fails[c] / len(runs) for c in ("safety", "booking", "knowledge", "style")},
    }


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("configs", nargs="*", help=f"default: all of {', '.join(CONFIGS)}")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--out", help="directory for logs and per-turn JSON (default: a temp dir)")
    args = p.parse_args()
    names = args.configs or list(CONFIGS)
    out_dir = Path(args.out or tempfile.mkdtemp(prefix="aster-bench-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"running {len(names)} configs × {args.runs} runs → {out_dir}")
    jobs = [(n, i) for n in names for i in range(1, args.runs + 1)]
    paths = await asyncio.gather(*(run_one(n, i, out_dir) for n, i in jobs))
    by_name: dict[str, list[Path]] = {}
    for (n, _), path in zip(jobs, paths):
        by_name.setdefault(n, []).append(path)

    print(f"\n{'config':<18}{'avg':>6}{'med':>6}{'p90':>6}{'max':>6}{'steps':>7}{'tokens':>8}"
          f"{'safety':>8}{'booking':>9}{'knowl.':>8}{'style':>7}   (failures per run)")
    for n in names:
        s = summarize(n, by_name[n])
        if s is None:
            print(f"{n:<18}  no results – see {out_dir}/{n}-1.log")
            continue
        print(f"{n:<18}{s['avg']:>6.1f}{s['median']:>6.1f}{s['p90']:>6.1f}{s['max']:>6.1f}{s['steps']:>7.1f}"
              f"{s['tokens']:>8.0f}{s['safety']:>8.1f}{s['booking']:>9.1f}{s['knowledge']:>8.1f}{s['style']:>7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
