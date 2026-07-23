# CLAUDE.md

## Project-Specific Guidelines

Velmo 2.0: support agent for a vintage/collector football-jersey shop, rebuilt around persistent per-user memory, input/output guardrails, and continuously measured quality (MLOps).

- Python 3.11 only, managed with `uv` (not pip/poetry) — `uv sync`, `uv run pytest`
- `pyproject.toml` enforces `mypy strict = true` and Ruff (line-length 100); keep new code passing both
- CI (`.github/workflows/quality.yml`) gates merges on the full chain: `ruff check`, `ruff format --check`, `mypy` (strict), `lint-imports` (isolation contracts), `pytest tests/acceptance/`, then the MLOps quality gate (`python -m velmo.mlops.score`, degraded/offline mode on PRs; real model on release/nightly, min-score 0.80). The four suites in `tests/acceptance/` (`test_memory.py`, `test_guardrails.py`, `test_mlops.py`, `test_business.py`) must stay green
- Core logic lives under `src/velmo/`: `memory/` (short+long term, isolation, forgetting), `guardrails/` (input/output filtering), `mlops/` (eval suites, versioning) — keep new code in the matching module rather than `agent.py`
- Long-term memory requirements are R1-R6 per `docs/reco_expert.md` — any change to `memory/` should be checked against those, not just against existing tests

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
