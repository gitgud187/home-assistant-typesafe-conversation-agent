# Experiment: an explicit "not listed" option on the entity question

Date: 2026-10-13. Raw answers: [`2026-10-13-not-listed-option.json`](2026-10-13-not-listed-option.json).
Reproduce with `scripts/experiment_not_listed.py` (command below).

## Question

When a user names one specific device that is not in the home, what does the model
answer on the `target_entity` question, and does a separate "not listed" option let
the router tell that case apart from a real group request ("all the lights")?

## Why it matters

`target_entity` has a single escape, `no_single_entity`, described as "a group of
things, a whole room, the whole house, or nothing". A request for a missing device
lands there too, and the router then falls back to the domain answer. In a live
check, "is the bathroom light on" (not exposed to Assist) queried every exposed
light and read them all out. The router has no signal that "a specific thing was
named and not found".

## Setup

| | |
|---|---|
| Server | OpenRouter, `POST https://openrouter.ai/api/v1/systemone` |
| Model | `typesafe/jev-1.13` (the version the thresholds in `const.py` were fitted to) |
| Home | `tests/fixtures/home.json`, synthetic, 29 entities, 8 areas |
| Speaker area | kitchen |
| Questions | the repository's own `build_questions()` output; only `target_entity` differs between variants |
| Runs | one request per (variant, sentence); 66 requests, 435,789 input tokens, about $0.018 |
| Latency | median 399 ms, max 566 ms per request |

### Variants

- **baseline**: unchanged.
- **A_not_listed**: adds option `not_listed` ("The request names one specific device
  that does not appear in `home.entities`, so none of the entries above is what the
  user means."), narrows the `no_single_entity` description to groups, rooms, the
  whole house or nothing, and adds a sentence to the instructions: choose
  `not_listed` when the user names one specific device and no entry is that device;
  do not pick the nearest-sounding entry.
- **B_not_listed_short**: adds only `not_listed` with the criterion "A specific
  device that is not in this home." Instructions and the other option are unchanged.

### Sentences

Ten "missing" sentences name a device the synthetic home does not have (sauna light,
toaster, porch light, basement light, attic fan, patio lamp, dryer, kitchen light
strip, guest room light, christmas tree lights). Twelve "control" sentences must keep
working: eight single devices (including an alias, "espresso machine", and a spelling
variant, "bed side lamp") and four group requests ("turn off the lights in the
kitchen", "turn off all the lights", "are any lights on in the living room", "turn
off everything"). The full list is in `CASES` in the script.

A verdict is **pass** if the top choice is the expected one (`not_listed` for missing;
the entity id for single-device controls; `no_single_entity` for group controls),
**fail** otherwise, and **WRONG-DEVICE** if a missing-device sentence picked a listed
entity, since that would act on the wrong thing.

## Results

Verdicts on the top choice:

| Variant | Missing: pass / fail / wrong device | Controls: pass / fail |
|---|---|---|
| baseline | 0 / 9 / 1 | 11 / 1 |
| A_not_listed | 8 / 1 / 1 | 12 / 0 |
| B_not_listed_short | 8 / 1 / 1 | 11 / 1 |

- **Baseline:** nine of ten missing sentences chose `no_single_entity`, eight of them
  at 0.94 to 1.00 confidence (the ninth, "guest room light", at 0.60). That is the
  bug, and the model is very sure of it. The tenth picked
  `light.kitchen_under_cabinet` at 0.78.
- **A and B** move most missing sentences to `not_listed`. A is more confident
  (0.87 to 1.00 on seven of its eight passes) than B (0.38 to 0.91 across its eight).
- **Controls:** A kept all twelve, including the group requests. In the baseline,
  "turn off the lights in the kitchen" picked a single light (`light.kitchen_ceiling`,
  0.61), and A turned that into `no_single_entity` (0.74). B left it as the baseline
  did.

### Would the router act on it?

The router only treats a Choice as solid at confidence 0.60 and a margin of 0.15
(`T_ENTITY`, `MIN_MARGIN`). Applying that to the missing sentences, with the margin
measured against `no_single_entity` only (an approximation):

| Variant | Solid `not_listed` | Not solid |
|---|---|---|
| A_not_listed | 7 of 10 | basement light (0.57), guest room (picked `no_single_entity`, 0.43) |
| B_not_listed_short | 6 of 10 | basement (picked `no_single_entity`), guest room (0.38), christmas lights (0.54) |

A non-solid answer still falls through to today's behaviour, so the router would
need a rule for it: when `not_listed` is the top choice but not solid, ask or say it
couldn't find the device and do not run a domain-wide query.

## The one wrong-device case

"turn off the kitchen light strip" chose `light.kitchen_under_cabinet` in every
variant (0.54 to 0.78). The synthetic kitchen has an under-cabinet light, which is
plausibly a strip, so this is arguably a reasonable match and a poor test case,
not a failure of the option. It does show that `not_listed` will not catch
near-matches, and that a wrong guess still reaches the confidence gate.

## Limits

- One request per cell. Repeatability was not checked, so small differences (A vs B
  on a single sentence) should not be over-read.
- A 29-entity synthetic home. A larger home with more near-matches may behave
  differently, and a home above the option cap uses the two-step device pick in
  `hierarchy.py`, which this did not exercise.
- Twenty-two sentences, written by hand, in English only.
- Only the `target_entity` answer was examined. The category, domain and action
  answers were recorded in the JSON but not analysed.

## Conclusion

An explicit `not_listed` option works on this model and this home. Variant A
(reworded `no_single_entity`, the new option, and the "do not pick the
nearest-sounding entry" instruction) was better than B on every measure here and
did not hurt group requests. It is a promising change but not yet shipped: it needs a
router rule for `not_listed`, a fallback for the non-solid cases, tests, and a check
on the real instance with the exposed lights.

## Reproduce

```sh
set -a; . ./.env; set +a
.venv/bin/python scripts/experiment_not_listed.py \
    --base-url https://openrouter.ai/api --model typesafe/jev-1.13
```

It overwrites the JSON next to this file and prints the summary. Use `--stem` to
write a new set of results instead.
