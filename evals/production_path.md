# Current production path (frozen — PROMPT 0)

Do not treat this as the target architecture. It records what the repo
actually runs today so hybrid-AI work can land without guessing.

## Web job path (`server.py`)

```
POST /api/jobs
  → stage footage/web_<id>/ (+ optional slate.mp4)
  → image→still_XX.mp4 via ffmpeg (_image_to_clip)
  → write note.txt
  → beats.extract_beat_grid if .beats.json missing
  → pipeline.split.run_split
  → pipeline.filter.filter_scenes
  → server._ensure_candidates (promote soft stills)
  → pipeline.sheet.build_contact_sheets
  → pipeline.select_ai.select_ai  (if ANTHROPIC_API_KEY)
       else pipeline.select_baseline.select_baseline
  → run._stage_render (resolve-props.mjs → Remotion ZlogFilm)
  → run._stage_grade (ffmpeg lut3d)
  → work/<project>/final.mp4
```

## CLI path (`run.py pipeline`)

```
split → filter → select_baseline → render → grade
```

No sheet / tag / select_ai on the CLI chain today.

## Intermediate artifacts under `work/<project>/`

| File / dir | Producer | Consumer |
|---|---|---|
| `note.txt` | `server.py` | captions (not Claude prompt today) |
| `segments.json` | `pipeline/split.py` | filter |
| `frames/<segment_id>.jpg` | split | filter, sheet, review |
| `candidates.json` | filter (+ `_ensure_candidates`) | sheet, select_* |
| `rejected_contact.jpg` | filter | human debug |
| `contact_sheets/sheet_*.jpg` | sheet | select_ai, tag (unused on server) |
| `contact_sheet_manifest.json` | sheet | select_ai, tag |
| `tags.json` / candidates.tags | tag.py (not on server path) | select_ai (optional) |
| `edl_ai.json` / `edl_baseline.json` | select_ai / select_baseline | render, grade, inspect-edl |
| `edl_resolved.json` | `render/resolve-props.mjs` | Remotion `--props` |
| `render.mp4` | Remotion | grade |
| `final.mp4` | `pipeline/grade.py` | `/api/jobs/.../video` |

Also used (outside `work/`):

| Path | Role |
|---|---|
| `footage/<project>/*` | staged uploads + `still_XX.mp4` |
| `assets/bgm/<track>.{wav,mp3}` + `.beats.json` | audio + beat grid |
| `assets/luts/<lut>.cube` | ffmpeg lut3d |
| `.zlog_jobs/<id>.json` | web job status |

## Known regressions captured under `evals/`

1. Raw user note used as opening caption title  
   Intent fixture: `감성적인 유튜브 브이로그로 만들어줘`
2. Fixed 4:3 letterbox frame only
3. Still-source modulo reuse via `expand_timeline_to_target`
4. Order-cycled Ken Burns (content-independent)
5. User note never enters Claude `_build_user_content`
6. No semantic evaluator / `tag.py` not on server path
7. CLAUDE.md / README disagree with `server.py`

Photo fixture (6 same-subject stills):

`evals/fixtures/photos_vlog_same_subject/`

Inspect an EDL:

```bash
python -m pipeline.inspect_edl work/<project>/edl_ai.json
python run.py inspect-edl evals/fixtures/edl_intent_caption_and_repeats.json
```
