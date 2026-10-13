# AGENTS.md

Home Assistant custom integration (`custom_components/typesafe_conversation/`) that
registers a conversation agent. For each utterance it makes **one** call to a
TypeSafe System One decision model (Jev) with the utterance and a catalogue of
Assist-exposed entities, gets typed probabilities back, and plain Python decides
what to do. See also [CONTRIBUTING.md](CONTRIBUTING.md) and the README's
*Development* section.

## Commands

```sh
python3.14 -m venv .venv          # Home Assistant requires Python 3.14.2+
.venv/bin/pip install "pytest-homeassistant-custom-component==0.13.367" syrupy ruff
.venv/bin/pip install "gazetteer-matcher==1.1.0" "hassil==3.12.1" "home-assistant-intents==2026.8.28"

.venv/bin/python -m pytest                              # whole suite, no network or API key
.venv/bin/python -m pytest tests/test_router.py -k name # one file / one test
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The harness version pins one Home Assistant core version, and the second `pip`
line holds that core's `conversation` requirements. Change them together (README
*Development* shows how to read them off the installed core). `live`-marked tests
need a real `TYPESAFE_API_KEY` and are not part of normal runs.

## Architecture

The request path crosses several files:

1. `agent.py` receives the utterance and takes the exposed-entity catalogue
   (`entities.py`). Homes larger than the server's option cap pick a device in two
   steps (`hierarchy.py`).
2. `questions.py` builds the single set of typed questions (choice / yes-no /
   score) and `system_one.py` sends them, with retries and a circuit breaker.
3. `router.py` turns the answers into a `Plan`. It is a **pure function** of the
   answers, which is why routing is tested by replaying recorded API responses from
   `tests/fixtures/answers/` and `tests/fixtures/scripted_answers/`.
4. `executor.py` and `actions.py` turn the `Plan` into Home Assistant intent calls
   and speech. Anything uncertain goes down a fallback ladder in `agent.py`:
   Home Assistant's sentence matcher, then the optional LLM (`llm_backend.py`),
   then an "I'm not sure" reply.

Numbers and times are pulled from the utterance in code (`extraction.py`); the
model only picks among candidates and never computes values.

## Constraints

- Keep `route()` pure. Pass new state in as an argument instead of using `hass`.
- Thresholds are named constants in `const.py`, fitted against recorded
  `jev-1.13.0` answers. Do not move one without evidence from
  `scripts/calibrate.py`.
- The pipeline skips TTS when a reply has no speech, so every success path must
  set some. Stock Home Assistant handlers that return states without speech
  (state queries) need speech added by this integration.
- Below the confidence and margin bars the agent asks or falls back with nothing
  done. Risky actions (unlock, open, disarm) go through the confirm gate.
- New behaviour needs a test that fails without it. Prefer replaying an existing
  fixture with one different input over recording a new one.
- Ruff config in `ruff.toml` is enforced in CI; `max-complexity` is 15.

## Code conventions

Ruff enforces style and complexity. These are what it can't:

- Every module starts with `from __future__ import annotations` and a docstring that
  says what the module is for. Comments explain *why* (a measurement, a constraint,
  a past failure), not what the line does. Match that.
- Fully annotate new code even though no type checker runs in CI. Use `Final`
  for constants, `@dataclass(slots=True)` for plain records and `StrEnum` for
  closed sets of names, as the existing modules do.
- The integration is async end to end. No blocking I/O or `time.sleep` on the event
  loop; use the shared aiohttp session.
- Catch specific exceptions. A broad `except Exception` is only for a config-flow or
  setup boundary that logs with `LOGGER.exception` and returns an error.
- Use the package `LOGGER` with lazy `%s` formatting. Per-request detail goes at
  `DEBUG`; nothing user-specific at `INFO` or above.
- Prefer extending a table or a named constant (`actions.py`, `const.py`) over
  adding another `if` branch.

## Working method

- After editing Python, run `pytest` and `ruff check . && ruff format --check .`
  before saying it works. Quote real output.
- For a bug fix, write the failing test first and confirm it fails for the right
  reason.
- Don't touch pinned versions, `hacs.json` or `manifest.json` unless asked. Don't
  add dependencies without saying why.
- Keep diffs to the one concern. Unrelated cleanups go in their own change.

## Data

- Committed fixtures are synthetic. Nothing captured from a real home (entity IDs,
  room names, traces, phrases) goes into code, tests, commits or PR text. Working
  notes go in `private/`, which is gitignored.
- The recorded API responses must not be used to train or distil a model.

## Git

This checkout is a fork (`origin`) of `the-sof/home-assistant-typesafe-conversation-agent`
(`upstream`), maintained as its own project. One concern per branch.

- `main` carries our changes. Releases are tags cut from it, starting at `0.5.0`
  (upstream is 0.4.x, so the numbers never collide). Bump
  `custom_components/typesafe_conversation/manifest.json` in the same commit as the
  change that earns the version.
- Keep the domain `typesafe_conversation` so an install replaces upstream in place.
- Pulling from `upstream` is fine; review what changed before merging it.
- Nothing goes back: no pushes, issues, PRs or comments on `the-sof/...`.
