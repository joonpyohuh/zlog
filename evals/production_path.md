# Current production path (PROMPT 9)

## Web / Studio job path (`server.py`)

```
POST /api/jobs  (note, files, quality_mode, dev_mode)
  → footage/web_<id>/ + upload_order.json
  → image→still_XX.mp4
  → note.txt
  → beats if missing
  → pipeline.product_pipeline.run_product_pipeline:
       split → evidence(+features) → filter → sheet
       → analyze_assets → director → plan_timeline
       → evaluate_plan → render → grade → audio_engine
  → work/<project>/final.mp4
```

Fallback: if hybrid AI fails or no `ANTHROPIC_API_KEY`, `select_baseline`.

## CLI path (`run.py pipeline`)

Same `run_product_pipeline` stages. Flags: `--quality-mode`, `--baseline`, `--intent`.

## Quality modes

| Mode | Analyze | Evaluate | Sol |
|---|---|---|---|
| economy | Haiku only | code only | no |
| balanced | Haiku + selective Sonnet | Luna + Claude revise | no |
| premium | Haiku + aggressive Sonnet | Luna + Claude + Sol | yes if needed |

## Artifacts

| File | Producer |
|---|---|
| `upload_order.json` | server upload |
| `segments.json` | split |
| `evidence_*`, `deterministic_features.json` | evidence |
| `candidates.json` | filter |
| `asset_analyses.json` | analyze_assets |
| `story_plan.json` | director |
| `timeline_plan.json`, `edl_ai.json` | plan_timeline |
| `plan_evaluation.json` | evaluate_plan |
| `pipeline_run.json` | product_pipeline telemetry summary |
| `render.mp4` / `final.mp4` / `audio_mix.wav` | Remotion / grade / audio |

YouTube taste overwrite: **disabled** unless `ZLOG_ALLOW_YOUTUBE_TASTE_OVERWRITE=1`.
