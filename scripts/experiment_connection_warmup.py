#!/usr/bin/env python
"""How much of a System One call's latency is the connection, and can it be warmed?

HA-Jev measured 250 to 580 ms for a warm call and 700 to 900 ms for the first call
after an idle spell. That gap could be connection setup (DNS, TCP, TLS) or the
server being cold; only the first could be avoided by opening a connection before the
user finishes speaking. This script separates them by timing the same real-sized
request four ways.

    set -a; . ./.env; set +a
    .venv/bin/python scripts/experiment_connection_warmup.py \\
        --base-url https://openrouter.ai/api --model typesafe/jev-1.13

Runs against the synthetic home. Writes ``docs/experiments/<stem>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp
from calibrate import ask, load_home

from custom_components.typesafe_conversation.const import DEFAULT_BASE_URL
from custom_components.typesafe_conversation.extraction import extract
from custom_components.typesafe_conversation.questions import build_questions

UTTERANCE = "turn off the kitchen ceiling light"


def build(home_path: Path):
    home, entities, areas, domains = load_home(home_path)
    extraction = extract(UTTERANCE, want_media=True, want_color=True)
    questions = build_questions(
        entities=entities,
        areas=areas,
        domains=domains,
        extraction=extraction,
        max_options=255,
    )
    state = {
        "request": {
            "text": UTTERANCE,
            "language": "en",
            "spoken_from_area": "kitchen",
            "local_time": "2026-09-20T22:40",
            "weekday": "Sunday",
        },
        "home": home,
    }
    return state, questions


def fresh_session() -> aiohttp.ClientSession:
    # force_close: every request opens a new connection, as a long-idle one would.
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True))


async def timed(session, key, model, state, questions, base_url) -> float:
    started = time.monotonic()
    await ask(session, key, model, state, questions, base_url=base_url, timeout=60)
    return (time.monotonic() - started) * 1000


async def warm_connection(session, key, base_url) -> float:
    """Open (and keep) a connection with a request that asks the model nothing."""
    started = time.monotonic()
    async with session.get(
        base_url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {key}"} if key else {},
    ) as response:
        await response.read()
    return (time.monotonic() - started) * 1000


def summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median_ms": round(statistics.median(values)),
        "min_ms": round(min(values)),
        "max_ms": round(max(values)),
    }


async def tiny_request(session, key, model, base_url) -> float:
    """The smallest real decision request: one word of state, one three-way choice."""
    tiny_state = {"request": {"text": "hi"}}
    tiny_questions = {
        "q": {
            "type": "choice",
            "instructions": "Pick one.",
            "criteria": {"a": None, "b": None, "c": None},
        }
    }
    return await timed(session, key, model, tiny_state, tiny_questions, base_url)


async def wake_test(args, key, state, questions) -> None:
    """Idle, then a real call alone (A) or after a tiny request 1.5 s earlier (B).

    Alternates A and B so slow drift in the service cancels out.
    """
    outcomes: dict[str, list[float]] = {"A_alone": [], "B_after_tiny_request": []}
    tiny_times: list[float] = []
    async with aiohttp.ClientSession() as session:
        await timed(session, key, args.model, state, questions, args.base_url)
        for _ in range(args.rounds):
            for variant in ("A_alone", "B_after_tiny_request"):
                await asyncio.sleep(args.idle)
                if variant == "B_after_tiny_request":
                    tiny_times.append(
                        await tiny_request(session, key, args.model, args.base_url)
                    )
                    await asyncio.sleep(1.5)
                outcomes[variant].append(
                    await timed(
                        session, key, args.model, state, questions, args.base_url
                    )
                )
                print(variant, round(outcomes[variant][-1]), flush=True)
    report = {
        "model": args.model,
        "base_url": args.base_url,
        "idle_seconds": args.idle,
        "summary": {name: summary(v) for name, v in outcomes.items()},
        "raw": {k: [round(x) for x in v] for k, v in outcomes.items()},
        "tiny_request_ms": [round(x) for x in tiny_times],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.stem}-wake.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))
    print("tiny request ms:", report["tiny_request_ms"])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="typesafe/jev-1.13")
    parser.add_argument("--home", type=Path, default=Path("tests/fixtures/home.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--stem", default="2026-10-13-connection-warmup")
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument(
        "--wake-test",
        action="store_true",
        help="after an idle spell, compare a real call alone with one preceded by a "
        "tiny model request ~1.5 s earlier (what a wake word could trigger)",
    )
    parser.add_argument("--idle", type=float, default=40, help="idle seconds")
    args = parser.parse_args()

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and args.base_url.rstrip("/") == DEFAULT_BASE_URL:
        raise SystemExit("TYPESAFE_API_KEY is not set (try: set -a; . ./.env; set +a)")

    state, questions = build(args.home)
    if args.wake_test:
        await wake_test(args, key, state, questions)
        return
    results: dict[str, list[float]] = {
        "new_connection_each_time": [],
        "reused_connection": [],
        "new_connection_after_prewarm": [],
        "prewarm_request_alone": [],
    }

    # 1. A brand-new connection for every call.
    for _ in range(args.rounds):
        async with fresh_session() as session:
            results["new_connection_each_time"].append(
                await timed(session, key, args.model, state, questions, args.base_url)
            )
        await asyncio.sleep(2)

    # 2. One connection reused back to back.
    async with aiohttp.ClientSession() as session:
        await timed(session, key, args.model, state, questions, args.base_url)
        for _ in range(args.rounds):
            results["reused_connection"].append(
                await timed(session, key, args.model, state, questions, args.base_url)
            )
            await asyncio.sleep(2)

    # 3. A new connection that was opened by a cheap request first.
    for _ in range(args.rounds):
        async with aiohttp.ClientSession() as session:
            results["prewarm_request_alone"].append(
                await warm_connection(session, key, args.base_url)
            )
            await asyncio.sleep(1)
            results["new_connection_after_prewarm"].append(
                await timed(session, key, args.model, state, questions, args.base_url)
            )
        await asyncio.sleep(2)

    # 4. How long a reused connection stays useful when left idle.
    idle: dict[str, float] = {}
    async with aiohttp.ClientSession() as session:
        await timed(session, key, args.model, state, questions, args.base_url)
        for seconds in (10, 30, 60, 90):
            await asyncio.sleep(seconds)
            idle[f"after_{seconds}s_idle"] = await timed(
                session, key, args.model, state, questions, args.base_url
            )

    report = {
        "model": args.model,
        "base_url": args.base_url,
        "utterance": UTTERANCE,
        "summary": {name: summary(v) for name, v in results.items()},
        "raw": {k: [round(x) for x in v] for k, v in results.items()},
        "idle_ms": {k: round(v) for k, v in idle.items()},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.stem}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))
    print(json.dumps(report["idle_ms"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
