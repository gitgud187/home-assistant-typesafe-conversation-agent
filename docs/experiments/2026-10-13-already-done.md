# Experiment: a command for a device that is already in that state

Date: 2026-10-13. Raw results: [`2026-10-13-already-done.json`](2026-10-13-already-done.json).
Reproduce with `scripts/experiment_already_done.py`.

## Question

HA-Jev measured a command's action confidence falling from 1.00 to about 0.25 to 0.31
when the device was already in the requested state ("turn on the lamp" with the lamp on),
because the sentence could then also read as a question. If the same happens here, a
harmless redundant command could fall to the fallback ladder instead of running. Does it?

## Setup

Model `typesafe/jev-1.13` through OpenRouter; synthetic home (`tests/fixtures/home.json`).
Six sentences, each asked with the device already in the requested state (`already_done`)
and in the opposite state (`needs_change`), twice each: 24 requests, about $0.007. The
real `route()` was run on every response.

Sentences: turn on the kitchen ceiling light, turn off the under cabinet light, turn on
the coffee maker, turn off the tv, turn on the hall light, turn off the bedroom fan.

## Results

| Situation | Requests | Routes | Action confidence (min / mean) | p(query) |
|---|---|---|---|---|
| already_done | 12 | 12 command | 0.91 / 0.98 | 0.00 |
| needs_change | 12 | 12 command | 0.97 / 0.99 | 0.00 |

Every request routed as a normal command, in both situations. The category answer was
`command` at 1.00 each time and gave the `query` option no probability at all.

## Conclusion

The effect HA-Jev reported does not appear here, so no change is made for it. The likely
reason is the question design: this integration asks "command or query" as a separate
category question and then asks the action question per domain, so the action question
never has a state-query option to spread probability onto.

## Limits

Six sentences, two repeats, a 29-entity synthetic home, English only, and states given
as `on`/`off`. Only lights, a switch, a media player and a fan were tried.

## A different problem this surfaced

For "turn off the tv" the action answer was `pause` (0.91 to 0.97), and the router
produced `media_player.pause` in all four requests. The action question for
`media_player` offers play, pause, next, previous, volume and mute options but no
`turn_on` or `turn_off` (`questions.py`), even though `actions.py` maps those to the
power intents. A request to power a TV off therefore resolves to pausing playback. This
is a separate finding and is not addressed by this experiment.
