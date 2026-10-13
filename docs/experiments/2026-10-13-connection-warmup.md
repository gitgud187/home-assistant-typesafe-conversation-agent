# Experiment: can a call be made faster by warming it up first?

Date: 2026-10-13. Raw results: [`2026-10-13-connection-warmup.json`](2026-10-13-connection-warmup.json)
and [`2026-10-13-connection-warmup-wake.json`](2026-10-13-connection-warmup-wake.json).
Reproduce with `scripts/experiment_connection_warmup.py`.

## Question

HA-Jev's measurements put a warm call at 250 to 580 ms and the first call after an
idle spell at 700 to 900 ms. If that gap is connection setup, opening a connection
when the wake word fires would hide it. If the service is cold, only a real request
would. Which is it, and how much could be saved?

## Setup

| | |
|---|---|
| Server | OpenRouter, `POST https://openrouter.ai/api/v1/systemone` |
| Model | `typesafe/jev-1.13` |
| Request | the repository's real question set for one sentence on the synthetic home, about 6,500 input tokens |
| Client | this development machine, not the Home Assistant host |
| Cost | under 1 cent in total |

## Part 1: the connection

Eight calls per case, 2 s apart.

| Case | Median | Min | Max |
|---|---|---|---|
| New connection for every call | 443 ms | 347 | 519 |
| One connection, reused | 345 ms | 310 | 592 |
| New connection, opened first by `GET /v1/models` | 359 ms | 332 | 492 |
| (the `GET /v1/models` request alone) | 131 ms | 99 | 176 |

Connection setup is worth about 100 ms here. Opening the connection first recovers
most of it (443 to 359 ms), so warming only the connection saves roughly 85 ms.

Single samples after an idle spell on a reused connection: 313 ms after 10 s, 845 ms
after 30 s, 666 ms after 60 s, 517 ms after 90 s. These are larger than the cost of
a fresh connection, which suggests the extra time is on the service side.

## Part 2: a tiny request before the real one

Six rounds, alternating (A) a real call after 40 s of idle and (B) a minimal decision
request (one word of state, one three-way choice) followed 1.5 s later by the same
real call. The 1.5 s stands in for the time between a wake word and the end of the
sentence.

| | A: real call alone | B: after a tiny request |
|---|---|---|
| Per round (ms) | 649, 486, 642, 432, 542, 543 | 289, 342, 446, 342, 327, 306 |
| Median | 542 ms | 334 ms |
| Min / max | 432 / 649 | 289 / 446 |

B was faster in 6 of 6 rounds. The paired difference had a median of about 205 ms
and ranged from 90 to 360 ms. The tiny request itself took 312 to 629 ms, but it
runs while the user is still speaking.

## Conclusion

- A model-side warm-up helps, by about 0.2 s on the median and up to 0.36 s on a
  cold call. It is not the half second the earlier figures suggested; much of that
  gap is variation, and a plain connection warm-up recovers only about 0.1 s.
- The effect is on the service side, not the connection, because both A and B reuse
  one connection.
- Sending the tiny request costs a fraction of a cent per thousand wake words
  (a handful of input tokens each).

## Limits

- Six rounds, one location, one model, one idle length (40 s). Longer idle gaps and
  the Home Assistant host's own network path were not measured.
- Variation between runs is large compared with the effect (A ranged 432 to 649).
- This measures the request, not a built feature. A real implementation would also
  need a trigger for "a command is about to arrive".
