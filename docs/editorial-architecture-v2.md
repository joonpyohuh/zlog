# Zlog Editorial Architecture v2

Zlog turns one long travel video or many clips into the same content model, then makes separate editorial decisions for long and short outputs. Rendering executes those decisions; it does not invent them.

## Product flow

```text
RAW MEDIA
  → deterministic shot boundaries and evidence frames
  → SceneUnderstanding
  → TripGraph (Trip → Day → Event → Scene)
  → EditingIntent
  → long and short HighlightDecision sets
  → separate EditTimeline plans
  → actionable CriticReport
  → legacy EDL adapter
  → Remotion / FFmpeg
```

The three responsibilities are deliberately separate:

- **Understanding** records observable subjects, actions, locations, time cues, reactions, quality limitations, and uncertainty. It does not decide the final edit.
- **Editorial intelligence** assigns roles, selects moments, orders them, decides duration, captions, and justified effects. Long and short are separate outputs.
- **Rendering** follows timeline array order and explicit commands. It never creates transitions, motion, captions, or music from an item's index.

## Contracts and artifacts

`pipeline/editorial_models.py` owns the v2 contracts. A project run writes:

| Artifact                                | Purpose                                                  |
| --------------------------------------- | -------------------------------------------------------- |
| `trip_graph.json`                       | Day/Event/Scene structure and unknowns                   |
| `editing_intent.json`                   | Explicit current prompt; unspecified values stay unknown |
| `highlight_decisions.json`              | Long/short inclusion decisions and reasons               |
| `timeline_long.json`                    | Editable long-form primary track                         |
| `timeline_short.json`                   | Editable short-form primary track                        |
| `critic_long.json`, `critic_short.json` | Repairable issues, not opaque scores                     |
| `edl_long.json`, `edl_short.json`       | Temporary adapter for the existing renderer              |

The current prompt has priority over current-project corrections, which have priority over learned historical preferences. Learned preferences must never silently override an explicit prompt.

## Editorial invariants

- Chronology is the default for travel progression. Short form may open on a grounded hook, but it must retain understandable context and a payoff.
- Technical rejection does not delete a scene from understanding. It marks the scene as `limited`, allowing context-critical footage to survive selection.
- Duration varies by editorial role, observable action, output type, and requested pacing. It is never derived from a fixed item-index pattern.
- Every non-cut effect has a content or intent reason. No reason means a clean cut.
- Captions require grounding. Day changes receive a time marker when capture dates support the boundary.
- Music is never auto-selected. Zlog returns an ordered recommendation list and search results; the user explicitly selects a track or keeps location sound.
- Missing evidence remains `unknown`. Zlog does not invent locations, relationships, dialogue, or emotion.

## Reference-learning environment

Reference videos should be ingested as observations, not copied as templates. For each reference, store:

1. scene boundaries and observable cues;
2. editorial role hypotheses;
3. selection, order, and duration decisions;
4. caption/effect/audio decisions with reasons;
5. confidence and unknowns;
6. human corrections and before/after timeline values;
7. failure cases where the reference pattern damaged clarity or intent.

Reference material supplied for learning should therefore include the original video, its intended audience/platform, any known creative brief, and — when available — the final edit or edit notes. Project corrections are stored as explicit feedback events so later preference learning can be measured instead of inferred from magic scores.

## Migration

The v2 timeline is authoritative. `timeline_to_edl()` is a temporary compatibility boundary around reusable Remotion and FFmpeg infrastructure. New editorial behavior belongs in the v2 planner/contracts; adding implicit renderer behavior or new order-based behavior to the adapter is prohibited.

Supabase migration `20260808_editorial_projects.sql` adds durable projects, source assets, editorial versions, long/short outputs, timeline revisions, render jobs, and correction events. Local JSON artifacts remain the development/runtime cache until the API is moved to those tables.
