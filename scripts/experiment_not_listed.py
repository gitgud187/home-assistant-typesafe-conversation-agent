#!/usr/bin/env python
"""Does an explicit "not listed" option stop Jev guessing at devices that don't exist?

The entity question has one escape, ``no_single_entity``, described as "a group of
things, a whole room, the whole house, or nothing". That merges "all the lights" with
"one specific device that isn't in the home", so a request for a missing device comes
back as a domain-wide target. This script asks the same sentences under three
wordings of the question and records which option Jev picks, so a change to
``questions.py`` can be argued from answers instead of guessed.

    set -a; . ./.env; set +a
    .venv/bin/python scripts/experiment_not_listed.py \\
        --base-url https://openrouter.ai/api --model typesafe/jev-1.13

It uses the synthetic home in ``tests/fixtures/home.json`` and writes the raw answers
and a summary next to the write-up in ``docs/experiments/``. Nothing here touches a
real Home Assistant.
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
    NO_SINGLE_ENTITY,
    Q_TARGET_ENTITY,
    build_questions,
)

NOT_LISTED = "not_listed"

# (utterance, kind, expected)
#   missing : names one specific device the synthetic home does not have
#   control : must still resolve as before. expected is an entity id, or
#             "group" for a request about several things or a whole room
CASES: list[tuple[str, str, str]] = [
    ("is the sauna light on", "missing", NOT_LISTED),
    ("turn off the toaster", "missing", NOT_LISTED),
    ("turn off the porch light", "missing", NOT_LISTED),
    ("is the basement light on", "missing", NOT_LISTED),
    ("turn on the attic fan", "missing", NOT_LISTED),
    ("dim the patio lamp", "missing", NOT_LISTED),
    ("is the dryer running", "missing", NOT_LISTED),
    ("turn off the kitchen light strip", "missing", NOT_LISTED),
    ("is the guest room light on", "missing", NOT_LISTED),
    ("turn on the christmas tree lights", "missing", NOT_LISTED),
    ("turn off the bathroom light", "control", "light.bathroom"),
    ("is the bedroom fan on", "control", "fan.bedroom"),
    ("start the espresso machine", "control", "switch.coffee_maker"),
    ("turn off the tv", "control", "media_player.tv"),
    ("close the living room shades", "control", "cover.lr_blinds"),
    ("turn on the floor lamp", "control", "light.lr_floor_lamp"),
    ("turn on the bed side lamp", "control", "light.bedside_lamp"),
    ("is the garage door open", "control", "cover.garage_door"),
    ("turn off the lights in the kitchen", "control", "group"),
    ("turn off all the lights", "control", "group"),
    ("are any lights on in the living room", "control", "group"),
    ("turn off everything", "control", "group"),
]

# How the three wordings differ. Everything else about the question set is the
# repository's own output, so the comparison changes one thing.
VARIANTS = ("baseline", "A_not_listed", "B_not_listed_short")


def apply_variant(questions: dict, variant: str) -> dict:
    """Return the question set with the entity question reworded for ``variant``."""
    if variant == "baseline":
        return questions
    entity = questions[Q_TARGET_ENTITY]
    criteria = dict(entity["criteria"])
    if variant == "A_not_listed":
        criteria[NO_SINGLE_ENTITY] = (
            "A group of things, a whole room, the whole house, or nothing in "
            "particular is being targeted."
        )
        criteria[NOT_LISTED] = (
            "The request names one specific device that does not appear in "
            "`home.entities`, so none of the entries above is what the user means."
        )
        instructions = (
            "Which single entity in `home.entities` does `request.text` target? "
            "Every option below is the `id` of one entry in `home.entities`; look "
            "that entry up for its name, other names, area, kind and current "
            "state. Match on what the user means rather than on exact wording: "
            "'get the coffee boiling' targets a coffee maker. Choose "
            f"'{NO_SINGLE_ENTITY}' when the request targets a group of things, a "
            f"whole room, the whole house, or nothing. Choose '{NOT_LISTED}' when "
            "the user names one specific device and no entry in `home.entities` "
            "is that device; do not pick the nearest-sounding entry."
        )
    elif variant == "B_not_listed_short":
        criteria[NOT_LISTED] = "A specific device that is not in this home."
        instructions = entity["instructions"]
    else:
        raise ValueError(variant)
    return {
        **questions,
        Q_TARGET_ENTITY: {**entity, "instructions": instructions, "criteria": criteria},
    }


def judge(kind: str, expected: str, answer: dict) -> str:
    """pass / fail / WRONG-DEVICE, from the entity answer alone."""
    choice = answer["choice"]
    if kind == "missing":
        if choice == NOT_LISTED:
            return "pass"
        # A listed device would be acted on, which is worse than a group target.
        return "fail" if choice == NO_SINGLE_ENTITY else "WRONG-DEVICE"
    if expected == "group":
        return "pass" if choice == NO_SINGLE_ENTITY else "fail"
    return "pass" if choice == expected else "fail"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="typesafe/jev-1.13")
    parser.add_argument("--home", type=Path, default=Path("tests/fixtures/home.json"))
    parser.add_argument("--area", default="kitchen")
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--stem", default="2026-10-13-not-listed-option")
    args = parser.parse_args()

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and args.base_url.rstrip("/") == DEFAULT_BASE_URL:
        raise SystemExit("TYPESAFE_API_KEY is not set (try: set -a; . ./.env; set +a)")

    home, entities, areas, domains = load_home(args.home)
    results: list[dict] = []
    input_tokens = 0

    async with aiohttp.ClientSession() as session:
        for variant in VARIANTS:
            for utterance, kind, expected in CASES:
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
                        "spoken_from_area": args.area,
                        "local_time": "2026-09-20T22:40",
                        "weekday": "Sunday",
                    },
                    "home": home,
                }
                body, latency = await ask(
                    session,
                    key,
                    args.model,
                    state,
                    questions,
                    base_url=args.base_url,
                    timeout=60,
                )
                answers = body["answers"]
                entity = answers[Q_TARGET_ENTITY]
                input_tokens += body.get("usage", {}).get("input_tokens", 0)
                row = {
                    "variant": variant,
                    "utterance": utterance,
                    "kind": kind,
                    "expected": expected,
                    "entity_choice": entity["choice"],
                    "entity_confidence": round(entity["confidence"], 3),
                    "p_not_listed": round(
                        entity["probabilities"].get(NOT_LISTED, 0), 3
                    ),
                    "p_no_single_entity": round(
                        entity["probabilities"].get(NO_SINGLE_ENTITY, 0), 3
                    ),
                    "domain": answers["target_domain"]["choice"],
                    "category": answers["category"]["choice"],
                    "verdict": judge(kind, expected, entity),
                    "latency_ms": round(latency),
                }
                results.append(row)
                print(
                    f"{variant:<20} {row['verdict']:<12} {utterance:<38} "
                    f"-> {row['entity_choice']} ({row['entity_confidence']:.2f})"
                )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.stem}.json").write_text(
        json.dumps(
            {
                "model": args.model,
                "base_url": args.base_url,
                "home": str(args.home),
                "input_tokens": input_tokens,
                "results": results,
            },
            indent=2,
        )
    )

    print()
    for variant in VARIANTS:
        rows = [r for r in results if r["variant"] == variant]
        for kind in ("missing", "control"):
            subset = [r for r in rows if r["kind"] == kind]
            counts = {
                v: sum(r["verdict"] == v for r in subset)
                for v in ("pass", "fail", "WRONG-DEVICE")
            }
            print(f"{variant:<20} {kind:<8} {counts}")
    print(f"\n{input_tokens} input tokens, about ${input_tokens * 42 / 1e9:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
