#!/usr/bin/env python
"""Does "turn on the lamp" read as a worse command when the lamp is already on?

HA-Jev measured the action confidence for a command falling from 1.00 to about 0.25
to 0.31 when the device was already in the requested state, because the sentence could
then also be a question. If the same happens here, a harmless redundant command could
fall to the fallback ladder instead of just running. This script asks the same sentence
twice, once with the device already in the requested state and once needing the change,
and records both the model's answers and what the real router does with them.

    set -a; . ./.env; set +a
    .venv/bin/python scripts/experiment_already_done.py \\
        --base-url https://openrouter.ai/api --model typesafe/jev-1.13

Synthetic home only. Writes ``docs/experiments/<stem>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp
from calibrate import ask, load_home

from custom_components.typesafe_conversation.const import DEFAULT_BASE_URL
from custom_components.typesafe_conversation.extraction import extract
from custom_components.typesafe_conversation.questions import build_questions
from custom_components.typesafe_conversation.router import route
from custom_components.typesafe_conversation.system_one import (
    SystemOneResponse,
    _parse_answers,
)

# (utterance, entity id, wanted state, domain)
CASES: list[tuple[str, str, str, str]] = [
    ("turn on the kitchen ceiling light", "light.kitchen_ceiling", "on", "light"),
    ("turn off the under cabinet light", "light.kitchen_under_cabinet", "off", "light"),
    ("turn on the coffee maker", "switch.coffee_maker", "on", "switch"),
    ("turn off the tv", "media_player.tv", "off", "media_player"),
    ("turn on the hall light", "light.hall", "on", "light"),
    ("turn off the bedroom fan", "fan.bedroom", "off", "fan"),
]
OPPOSITE = {"on": "off", "off": "on"}
REPEATS = 2


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="typesafe/jev-1.13")
    parser.add_argument("--home", type=Path, default=Path("tests/fixtures/home.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--stem", default="2026-10-13-already-done")
    args = parser.parse_args()

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and args.base_url.rstrip("/") == DEFAULT_BASE_URL:
        raise SystemExit("TYPESAFE_API_KEY is not set (try: set -a; . ./.env; set +a)")

    base_home, entities, areas, domains = load_home(args.home)
    entities_by_id = {e.entity_id: e for e in entities}
    available = frozenset(domains)
    rows: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for utterance, entity_id, wanted, domain in CASES:
            extraction = extract(
                utterance,
                want_media="media_player" in domains,
                want_color="light" in domains,
            )
            questions = build_questions(
                entities=entities,
                areas=areas,
                domains=domains,
                extraction=extraction,
                max_options=255,
            )
            for situation, device_state in (
                ("already_done", wanted),
                ("needs_change", OPPOSITE[wanted]),
            ):
                home = copy.deepcopy(base_home)
                for entity in home["entities"]:
                    if entity["id"] == entity_id:
                        entity["state"] = device_state
                state = {
                    "request": {
                        "text": utterance,
                        "language": "en",
                        "spoken_from_area": "kitchen",
                        "local_time": "2026-09-20T22:40",
                        "weekday": "Sunday",
                    },
                    "home": home,
                }
                for repeat in range(REPEATS):
                    body, _latency = await ask(
                        session,
                        key,
                        args.model,
                        state,
                        questions,
                        base_url=args.base_url,
                        timeout=60,
                    )
                    answers = body["answers"]
                    response = SystemOneResponse(
                        model=body.get("model", args.model),
                        answers=_parse_answers(body),
                        input_tokens=0,
                        output_tokens=0,
                        latency_ms=0.0,
                        raw=body,
                    )
                    plan = route(
                        response,
                        entities_by_id=entities_by_id,
                        extraction=extraction,
                        speaker_area_id="kitchen",
                        available_domains=available,
                    )
                    action = answers.get(f"action_{domain}", {})
                    probs = action.get("probabilities", {})
                    top = max(probs, key=probs.get) if probs else None
                    rows.append(
                        {
                            "utterance": utterance,
                            "situation": situation,
                            "repeat": repeat,
                            "category": answers["category"]["choice"],
                            "category_confidence": round(
                                answers["category"]["confidence"], 3
                            ),
                            "action_top": top,
                            "action_confidence": round(action.get("confidence", 0), 3),
                            "p_turn_on": round(probs.get("turn_on", 0), 3),
                            "p_turn_off": round(probs.get("turn_off", 0), 3),
                            "p_query": round(
                                answers["category"]["probabilities"].get("query", 0), 3
                            ),
                            "route": plan.route.value,
                            "route_reason": plan.reason,
                        }
                    )
                    r = rows[-1]
                    print(
                        f"{situation:<13} {utterance:<36} cat={r['category']} "
                        f"({r['category_confidence']:.2f}) action={r['action_top']} "
                        f"({r['action_confidence']:.2f}) route={r['route']}",
                        flush=True,
                    )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.stem}.json").write_text(
        json.dumps({"model": args.model, "results": rows}, indent=2)
    )
    print()
    for situation in ("already_done", "needs_change"):
        subset = [r for r in rows if r["situation"] == situation]
        routes: dict[str, int] = {}
        for r in subset:
            routes[r["route"]] = routes.get(r["route"], 0) + 1
        mean_conf = sum(r["action_confidence"] for r in subset) / len(subset)
        mean_query = sum(r["p_query"] for r in subset) / len(subset)
        print(
            f"{situation:<13} n={len(subset)} routes={routes} "
            f"mean action confidence {mean_conf:.2f}, mean p(query) {mean_query:.2f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
