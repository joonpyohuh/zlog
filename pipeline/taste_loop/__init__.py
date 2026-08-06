"""Taste loop — collect the founder's edit preferences as comparison data.

The loop: take one media set, render four variants that differ along exactly
one axis, pick the favourite, log the pick. Once enough picks exist per axis,
narrow that axis's allowed range in the StyleProfile by plain statistics.

No model is trained here. This layer sits *on top of* the existing pipeline:
it reads a base EDL that the normal pipeline already produced and rewrites
one axis's worth of fields on it. It never re-runs planning, never invents a
timestamp, and never bypasses the beat grid (see CLAUDE.md 대원칙 2/3).
"""
