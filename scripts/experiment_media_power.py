#!/usr/bin/env python
"""Can the media_player action question tell "turn off the TV" from "pause it"?

The ``action_media_player`` question offers play, pause, next, previous, volume, mute
and search options, but no turn_on or turn_off, so "turn off the tv" comes back as
``pause`` and the router produces ``media_player.pause``. This script asks the same
sentences under three wordings of the question and records the answer and the action
the real router would take.

    set -a; . ./.env; set +a
    .venv/bin/python scripts/experiment_media_power.py \\
        --base-url https://openrouter.ai/api --model typesafe/jev-1.13

Synthetic home only. Writes ``docs/experiments/<stem>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
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
from custom_components.typesafe_conversation.questions import (
    action_question_id,
    build_questions,
)
from custom_components.typesafe_conversation.router import route
from custom_components.typesafe_conversation.system_one import (
    SystemOneResponse,
    _parse_answers,
)

DOMAIN = "media_player"

# (utterance, acceptable actions, kind)
#   power    : the user means the device's power; only turn_on/turn_off is right
#   playback : the user means what is playing; only play/pause/... is right
#   either   : genuinely ambiguous (a speaker); both readings are acceptable
#   other    : unrelated to power, must not move
CASES: list[tuple[str, set[str], str]] = [
    ("turn off the tv", {"turn_off"}, "power"),
    ("turn on the tv", {"turn_on"}, "power"),
    ("switch off the television", {"turn_off"}, "power"),
    ("shut the tv off", {"turn_off"}, "power"),
    ("power on the tv", {"turn_on"}, "power"),
    ("pause the tv", {"pause"}, "playback"),
    ("pause the movie", {"pause"}, "playback"),
    ("stop the music", {"pause"}, "playback"),
    ("turn the music off", {"pause"}, "playback"),
    ("resume the movie", {"play"}, "playback"),
    ("unpause the tv", {"play"}, "playback"),
    ("turn off the living room speaker", {"turn_off", "pause"}, "either"),
    ("turn the speaker off", {"turn_off", "pause"}, "either"),
    ("mute the tv", {"mute"}, "other"),
    ("turn it up a bit", {"louder"}, "other"),
    ("play some jazz in the living room", {"search_and_play"}, "other"),
]

VARIANTS = ("baseline", "P1_power_described", "P2_power_short")


def apply_variant(questions: dict, variant: str) -> dict:
    if variant == "baseline":
        return questions
    key = action_question_id(DOMAIN)
    question = questions[key]
    criteria = dict(question["criteria"])
    if variant == "P1_power_described":
        criteria["turn_on"] = (
            "Power the device itself on, for example 'turn on the TV'."
        )
        criteria["turn_off"] = (
            "Power the device itself off, for example 'turn off the TV'."
        )
        criteria["pause"] = (
            "Pause, stop or halt what is playing, without switching the device off."
        )
    elif variant == "P2_power_short":
        criteria["turn_on"] = "Switch it on."
        criteria["turn_off"] = "Switch it off."
    else:
        raise ValueError(variant)
    return {**questions, key: {**question, "criteria": criteria}}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="typesafe/jev-1.13")
    parser.add_argument("--home", type=Path, default=Path("tests/fixtures/home.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--stem", default="2026-10-13-media-power")
    args = parser.parse_args()

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and args.base_url.rstrip("/") == DEFAULT_BASE_URL:
        raise SystemExit("TYPESAFE_API_KEY is not set (try: set -a; . ./.env; set +a)")

    home, entities, areas, domains = load_home(args.home)
    entities_by_id = {e.entity_id: e for e in entities}
    available = frozenset(domains)
    rows: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for variant in VARIANTS:
            for utterance, acceptable, kind in CASES:
                extraction = extract(
                    utterance,
                    want_media="media_player" in domains,
                    want_color="light" in domains,
                )
                questions = apply_variant(
                    build_questions(
                        entities=entities,
                        areas=areas,
                        domains=domains,
                        extraction=extraction,
                        max_options=255,
                    ),
                    variant,
                )
                state = {
                    "request": {
                        "text": utterance,
                        "language": "en",
                        "spoken_from_area": "living_room",
                        "local_time": "2026-09-20T22:40",
                        "weekday": "Sunday",
                    },
                    "home": home,
                }
                body, _ = await ask(
                    session,
                    key,
                    args.model,
                    state,
                    questions,
                    base_url=args.base_url,
                    timeout=60,
                )
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
                    speaker_area_id="living_room",
                    available_domains=available,
                )
                action = body["answers"].get(action_question_id(DOMAIN), {})
                probs = action.get("probabilities", {})
                top = max(probs, key=probs.get) if probs else None
                rows.append(
                    {
                        "variant": variant,
                        "utterance": utterance,
                        "kind": kind,
                        "acceptable": sorted(acceptable),
                        "action_top": top,
                        "action_confidence": round(action.get("confidence", 0), 3),
                        "p_top": round(probs.get(top, 0), 3) if top else 0,
                        "p_turn_off": round(probs.get("turn_off", 0), 3),
                        "p_pause": round(probs.get("pause", 0), 3),
                        "route": plan.route.value,
                        "plan_reason": plan.reason,
                        "pass": top in acceptable,
                    }
                )
                r = rows[-1]
                print(
                    f"{variant:<19} {'pass' if r['pass'] else 'FAIL':<5} "
                    f"{utterance:<36} -> {r['action_top']} ({r['p_top']:.2f}) "
                    f"route={r['route']} {r['plan_reason']}",
                    flush=True,
                )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.stem}.json").write_text(
        json.dumps({"model": args.model, "results": rows}, indent=2)
    )
    print()
    for variant in VARIANTS:
        for kind in ("power", "playback", "either", "other"):
            subset = [r for r in rows if r["variant"] == variant and r["kind"] == kind]
            passed = sum(r["pass"] for r in subset)
            print(f"{variant:<19} {kind:<9} {passed}/{len(subset)}")


if __name__ == "__main__":
    asyncio.run(main())
