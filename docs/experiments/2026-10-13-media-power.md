# Experiment: powering a media player on and off

Date: 2026-10-13. Raw answers: [`2026-10-13-media-power.json`](2026-10-13-media-power.json).
Reproduce with `scripts/experiment_media_power.py`.

## Question

The `action_media_player` question offered play, pause, next, previous, volume, mute and
search, with no power option. "Turn off the tv" therefore came back as `pause`, and the
router produced `media_player.pause`. Does offering `turn_on` and `turn_off` fix that
without breaking playback requests?

## Setup

`typesafe/jev-1.13` through OpenRouter, the synthetic home, speaker in the living room.
Sixteen sentences under three wordings of the question, one request each (48 requests,
about $0.013). The real `route()` was run on every response.

- **baseline**: unchanged.
- **P1_power_described**: adds `turn_on` ("Power the device itself on, for example 'turn
  on the TV'.") and `turn_off` (likewise), and rewords `pause` to "Pause, stop or halt
  what is playing, without switching the device off."
- **P2_power_short**: adds `turn_on` ("Switch it on.") and `turn_off` ("Switch it off."),
  the wording the other domains use, and leaves `pause` as it was.

Sentences: five about power ("turn off the tv", "turn on the tv", "switch off the
television", "shut the tv off", "power on the tv"), six about playback ("pause the tv",
"pause the movie", "stop the music", "turn the music off", "resume the movie", "unpause
the tv"), two about a speaker where both readings are acceptable, and three unrelated
("mute the tv", "turn it up a bit", "play some jazz in the living room").

## Results

Correct top action, by kind of sentence:

| Variant | Power | Playback | Speaker (either) | Unrelated |
|---|---|---|---|---|
| baseline | 0 / 5 | 6 / 6 | 2 / 2 | 3 / 3 |
| P1_power_described | 5 / 5 | 5 / 6 | 2 / 2 | 3 / 3 |
| P2_power_short | 5 / 5 | 5 / 6 | 2 / 2 | 3 / 3 |

- **Baseline:** every power sentence failed, with confidence 0.90 to 0.97. "Turn off the
  tv" became `pause`; "turn on the tv" became `play` (an unpause), which would not
  switch a TV on.
- **P1 and P2:** all five power sentences were right at 0.98 to 1.00 (P2) or 1.00 (P1).
- **The one playback miss, in both:** "turn the music off" became `turn_off` (0.91 for P2,
  0.69 for P1) where `pause` was expected. For a speaker either is a reasonable reading,
  and "stop the music" still resolved to `pause` (0.96 to 0.99).
- The two speaker sentences ("turn off the living room speaker") now resolve to
  `turn_off` where they were `pause`.

P2 was chosen: the same pass counts, higher confidence on the ambiguous sentence, and it
matches the wording every other domain uses.

## What shipped (v0.5.3)

`turn_on` and `turn_off` are offered for media players (P2 wording). Because many media
players are software endpoints with no power control, and Home Assistant rejects
`turn_off` for them while counting a matched area as a success, the router uses power
only when a **single resolved player** reports the `TURN_ON` or `TURN_OFF` feature.
Otherwise it pauses or resumes, which is what every media request did before. This is
the behaviour the earlier code comment was protecting. The 128 and 256 feature numbers
are pinned to `MediaPlayerEntityFeature` by a test.

Fixtures: three new recordings (turn off, turn on and pause the tv) and the five
media-centred ones re-recorded with the shipped question. Re-recording all 53 flipped
13 answer choices on questions the change did not touch, mostly near-ties, and pushed
one test across a threshold ("what's the weather", information 0.66 to 0.61), which is
run-to-run noise rather than an effect. The rest were left as recorded.

## Limits

- One request per cell; repeatability was not checked.
- A synthetic home with one TV and one speaker; real players differ in which features
  they report.
- Only the answer was tested. The router's fallback was exercised on recorded answers
  and synthetic feature flags, not on a real player that rejects power.
- English only, 16 sentences.
