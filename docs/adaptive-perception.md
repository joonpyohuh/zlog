# Adaptive perception engine

## Purpose

Zlog keeps PySceneDetect/FFmpeg as the owner of source timecodes and uses local evidence before any model call. The perception engine turns existing artifacts into compact, queryable event memory for the director. It does not replace the EDL or renderer.

```text
segments + evidence + deterministic features
  -> local scan
  -> weighted boundary proposals
  -> soft category routing
  -> goal-conditioned boundary judgments
  -> non-destructive hierarchy
  -> bounded agent tools
```

Each arrow writes a validated JSON artifact and has its own deterministic cache key. Keys include input artifact content, relevant configuration, schema version, and extractor version.

## Implemented now

`pipeline.adaptive_perception` runs five local passes:

1. `perception_scan.json` records source metadata, selected frame timestamps, and factorized lighting, composition, motion, audio, and quality features. It reuses `evidence.py`; it does not decode every frame again.
2. `boundary_candidates.json` proposes adjacent-shot boundaries from configurable shot, visual, motion, lighting, audio, and modular prediction-error scores.
3. `perception_routes.json` assigns soft category probabilities and selects only relevant feature families for each segment.
4. `boundary_decisions.json` applies the `EditingGoal` to split, merge, or retain uncertainty. Ambiguous cases become explicit escalation requests.
5. `hierarchical_timeline.json` preserves Frame -> Shot -> Atomic Event -> Semantic Scene -> Narrative Beat -> Chapter nodes and every source range. Higher-level merging never removes lower-level nodes.

`perception_cost_report.json` reports sampled frames, inspected duration, per-stage latency, cache hits/misses, escalations, calls, tokens, and estimated cost. Frame and processing budgets produce an explicit `degraded` status when exceeded. The implemented path makes zero provider calls and therefore has zero provider cost.

The existing `product_pipeline` runs perception after filtering and before model analysis. Existing Anthropic/OpenAI adapters remain the provider boundary for later selective inspection; no credentials or provider defaults changed.

## Goal conditioning

`EditingGoal` carries target duration, platform, style, pacing, narrative preference, dialogue preservation, and ambient-audio preservation. The current judgment policy adjusts semantic split thresholds by pacing and target duration. Cache keys include the goal, so a short fast edit and a long slow edit can safely produce different hierarchy artifacts from the same evidence.

## Agent tools

`PerceptionToolbox` validates versioned requests and caps results at 20. It supports event search, summary, segment/neighbor lookup, local clip/lighting/motion/audio inspection, comparison, category-vector similarity, existing boundary/merge judgment retrieval, and cost lookup.

`inspect_clip` returns bounded source ranges and evidence IDs. It never triggers a paid call. Expression inspection returns an explicit unavailable status instead of guessing.

```python
from pathlib import Path
from pipeline.perception_tools import run_tool

result = run_tool(
    Path("work/adaptive_perception_fixture"),
    {"tool": "search_events", "arguments": {"query": "activity", "topK": 5}},
)
```

## Evaluation

Run the complete local fixture:

```powershell
uv run python evals/run_perception_fixture.py
```

It creates an ignored 4.5-second MP4, runs scene splitting, evidence extraction, filtering, every perception pass, a second cache-only run, bounded tool lookup, and boundary evaluation. Labels live in `evals/fixtures/perception_boundaries.json`. The evaluation writes precision, recall, F1, boundary error, hierarchy consistency, coverage, duplicate rate, cache behavior, latency, calls, tokens, and cost to `perception_evaluation.json`.

## Scaffolded interfaces

- Ambiguous boundaries contain bounded `EscalationRequest` records compatible with a future visual-inspection adapter. They are `budget_blocked` by default or `provider_not_configured`; they are not executed.
- `UserEditFeedback` validates future split, merge, boundary, duration, removal, restoration, and preference corrections. No training loop consumes it yet.
- Separate feature groups reserve interpretable people, emotion, semantic, and composition routes without serializing opaque embedding vectors into prompts.

## Known limitations

- Atomic events are shot-level candidates; action-level sub-shot splitting is not implemented.
- Soft categories are deterministic routing priors, not a trained classifier.
- Zero RMS cannot yet distinguish true silence from an undecodable or missing audio track.
- Transcription, speakers, faces, objects, expressions, and embeddings are not produced because no existing local provider supports them.
- The MVP chapter policy groups one input episode into one chapter. Narrative roles use the existing Zlog Soft Flow positional prior.
- Perception artifacts are available through tools, but the current director prompt does not yet invoke the toolbox dynamically.

## Next highest-value extension

Add one mockable, budget-aware visual inspection capability to the existing provider protocol, then let the director request it only for `EscalationRequest` ranges. This converts unresolved expression/object/action uncertainty into better editing decisions without returning to whole-video model calls.
