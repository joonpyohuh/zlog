# render/

Remotion (TypeScript) project — not created yet.

Will consume `work/<run_id>/edl.json` plus the assets under `assets/`
(BGM, `.cube` LUTs) and render the final mp4: 4:3 letterbox crop, CCD/Y2K
color grade via ffmpeg `lut3d`, and the "directed by zlog" ending credit
in the last 2 seconds. All deterministic — no AI calls in this stage.
