"""Taste loop CLI.

    python -m pipeline.taste_loop.cli register-media-set --id trip_a --project demo_01 --bgm demo_track
    python -m pipeline.taste_loop.cli media-sets
    python -m pipeline.taste_loop.cli start-round --axis avg_cut_duration
    python -m pipeline.taste_loop.cli rounds
    python -m pipeline.taste_loop.cli pick <round_id> --winner <timeline_id>
    python -m pipeline.taste_loop.cli pick <round_id> --none-of-these
    python -m pipeline.taste_loop.cli feedback <timeline_id> --at 3.2 --verdict good

Profile narrowing lives in `python -m pipeline.taste_loop.update_profile`.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from pipeline.taste_loop.axes import TasteAxis, all_axes
from pipeline.taste_loop.feedback import build_feedback
from pipeline.taste_loop.media_sets import (
    load_registry,
    next_media_set_for_round,
    register_media_set,
)
from pipeline.taste_loop.render_batch import render_variants
from pipeline.taste_loop.store import ComparisonRound, get_store, new_round_id
from pipeline.taste_loop.style_profile import load_latest
from pipeline.taste_loop.variants import generate_variants

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@click.group()
def cli() -> None:
    """Collect edit-preference data by four-way comparison."""


@cli.command("register-media-set")
@click.option("--id", "media_set_id", required=True, help="stable id used in the round log")
@click.option("--project", required=True, help="existing pipeline project (work/<project>/)")
@click.option("--bgm", "bgm_track", required=True, help="BGM stem under assets/bgm/")
@click.option("--label", default="", help="human label for the UI")
@click.option("--replace", is_flag=True, default=False)
def register_cmd(
    media_set_id: str, project: str, bgm_track: str, label: str, replace: bool
) -> None:
    """Register a footage bundle that rounds can be run against."""
    item = register_media_set(
        media_set_id=media_set_id,
        project=project,
        bgm_track=bgm_track,
        label=label,
        replace=replace,
    )
    click.echo(f"registered {item.media_set_id} -> project={item.project} bgm={item.bgm_track}")
    try:
        click.echo(f"  base EDL: {item.base_edl_path(REPO_ROOT)}")
    except FileNotFoundError as exc:
        click.echo(f"  WARNING: {exc}")


@cli.command("media-sets")
def media_sets_cmd() -> None:
    """List registered media sets."""
    registry = load_registry()
    if not registry.sets:
        click.echo("(none registered)")
        return
    for item in registry.sets:
        try:
            base = str(item.base_edl_path(REPO_ROOT).relative_to(REPO_ROOT))
        except (FileNotFoundError, ValueError) as exc:
            base = f"MISSING ({exc})"
        click.echo(f"{item.media_set_id:16} project={item.project:14} bgm={item.bgm_track:14} base={base}")


@cli.command("axes")
def axes_cmd() -> None:
    """List the axes a round can shake, with their allowed ranges."""
    from pipeline.taste_loop.axes import REGISTRY

    profile = load_latest()
    click.echo(f"StyleProfile v{profile.version}")
    for axis, spec in REGISTRY.items():
        current = profile.range_for(axis)
        click.echo(
            f"{axis.value:22} allowed=[{spec.allowed.low}, {spec.allowed.high}] "
            f"current=[{current.low}, {current.high}]  ({spec.unit})"
        )


@cli.command("start-round")
@click.option(
    "--axis",
    type=click.Choice([a.value for a in all_axes()]),
    required=True,
)
@click.option("--media-set", "media_set_id", default=None, help="default: least recently used")
@click.option("--store", "backend", default=None, help="local | supabase")
@click.option("--scale", type=float, default=None, help="remotion --scale (e.g. 0.5 for speed)")
@click.option("--concurrency", type=int, default=None)
@click.option("--no-render", is_flag=True, default=False, help="build variants but skip rendering")
def start_round_cmd(
    axis: str,
    media_set_id: str | None,
    backend: str | None,
    scale: float | None,
    concurrency: int | None,
    no_render: bool,
) -> None:
    """Build 4 variants along one axis, render them, and log the round."""
    store = get_store(backend)
    registry = load_registry()
    if not registry.sets:
        raise click.ClickException(
            "no media sets registered — run `register-media-set` first"
        )

    if media_set_id:
        media_set = registry.get(media_set_id)
    else:
        previous = [r.media_set_id for r in store.list_rounds(limit=10_000)]
        media_set = next_media_set_for_round(registry, previous)
        click.echo(f"media set (least recently used): {media_set.media_set_id}")

    profile = load_latest()
    round_id = new_round_id()
    timelines = generate_variants(
        media_set, profile, TasteAxis(axis), round_id=round_id, repo_root=REPO_ROOT
    )

    click.echo(f"round {round_id}  axis={axis}  media_set={media_set.media_set_id}")
    for timeline in timelines:
        click.echo(
            f"  [{timeline.variant_index}] {timeline.timeline_id}  {axis}={timeline.axis_value}  "
            f"clips={timeline.clip_count} captions={timeline.caption_count} "
            f"dur={timeline.total_duration_sec}s"
        )

    renders = []
    if not no_render:
        renders = render_variants(
            timelines, repo_root=REPO_ROOT, scale=scale, concurrency=concurrency,
            on_progress=click.echo,
        )
        failed = [r for r in renders if not r.ok]
        if failed:
            click.echo(f"\n{len(failed)}/{len(renders)} variants FAILED to render:")
            for record in failed:
                click.echo(f"  {record.timeline_id}: {record.error}")

    round_ = ComparisonRound(
        id=round_id,
        media_set_id=media_set.media_set_id,
        axis=TasteAxis(axis),
        candidates=timelines,
        renders=renders,
        style_profile_version=profile.version,
        created_at=timelines[0].created_at,
    )
    store.save_round(round_)
    click.echo(f"\nlogged round {round_id}")


@cli.command("rerender")
@click.argument("round_id")
@click.option("--store", "backend", default=None)
@click.option("--scale", type=float, default=None)
@click.option("--concurrency", type=int, default=None)
def rerender_cmd(
    round_id: str, backend: str | None, scale: float | None, concurrency: int | None
) -> None:
    """Re-render only the variants of a round that failed.

    Renders are flaky under load (see render_batch.RENDER_ATTEMPTS); a round
    should not have to be regenerated — and its variants re-sampled — just
    because one video did not come out.
    """
    store = get_store(backend)
    round_ = store.get_round(round_id)
    if round_ is None:
        raise click.ClickException(f"no round {round_id!r}")

    failed_ids = {r.timeline_id for r in round_.renders if not r.ok}
    if not failed_ids:
        click.echo(f"round {round_id}: all {len(round_.renders)} variants already rendered")
        return

    retry = [c for c in round_.candidates if c.timeline_id in failed_ids]
    click.echo(f"re-rendering {len(retry)} failed variant(s) of round {round_id}")
    fresh = render_variants(
        retry, repo_root=REPO_ROOT, scale=scale, concurrency=concurrency, on_progress=click.echo
    )

    by_id = {r.timeline_id: r for r in fresh}
    round_.renders = [by_id.get(r.timeline_id, r) for r in round_.renders]
    store.save_round(round_)

    still_failed = [r for r in round_.renders if not r.ok]
    ok_count = len(round_.renders) - len(still_failed)
    click.echo(f"round {round_id}: {ok_count}/{len(round_.renders)} variants rendered")
    for record in still_failed:
        click.echo(f"  still failing: {record.timeline_id}: {record.error}")


@cli.command("rounds")
@click.option("--store", "backend", default=None)
@click.option("--limit", type=int, default=50)
def rounds_cmd(backend: str | None, limit: int) -> None:
    """List logged rounds."""
    for round_ in get_store(backend).list_rounds(limit=limit):
        if round_.rejected_all:
            outcome = "넷 다 별로"
        elif round_.winner_id:
            outcome = f"winner={round_.winner_id} value={round_.winning_value()}"
        else:
            outcome = "(unresolved)"
        ok = sum(1 for r in round_.renders if r.ok)
        click.echo(
            f"{round_.id}  {round_.axis.value:22} {round_.media_set_id:12} "
            f"renders={ok}/{len(round_.renders)}  {outcome}"
        )


@cli.command("pick")
@click.argument("round_id")
@click.option("--winner", "winner_id", default=None, help="timeline_id of the chosen variant")
@click.option("--none-of-these", is_flag=True, default=False, help='"넷 다 별로"')
@click.option("--store", "backend", default=None)
def pick_cmd(round_id: str, winner_id: str | None, none_of_these: bool, backend: str | None) -> None:
    """Record which variant won (or that none did)."""
    if bool(winner_id) == bool(none_of_these):
        raise click.ClickException("pass exactly one of --winner or --none-of-these")
    round_ = get_store(backend).record_pick(
        round_id, None if none_of_these else winner_id, rejected_all=none_of_these
    )
    if round_.rejected_all:
        click.echo(f"round {round_id}: 넷 다 별로 recorded")
    else:
        click.echo(
            f"round {round_id}: winner={round_.winner_id} "
            f"{round_.axis.value}={round_.winning_value()}"
        )


@cli.command("feedback")
@click.argument("timeline_id")
@click.option("--at", "timestamp_sec", type=float, required=True, help="seconds into the video")
@click.option("--verdict", type=click.Choice(["good", "bad"]), required=True)
@click.option("--comment", default=None)
@click.option("--store", "backend", default=None)
def feedback_cmd(
    timeline_id: str,
    timestamp_sec: float,
    verdict: str,
    comment: str | None,
    backend: str | None,
) -> None:
    """Record a good/bad mark at one moment of a rendered variant."""
    store = get_store(backend)
    timeline = None
    for round_ in store.list_rounds(limit=10_000):
        found = round_.timeline(timeline_id)
        if found is not None:
            timeline = found
            break
    if timeline is None:
        raise click.ClickException(f"no timeline {timeline_id!r} in any logged round")

    row = build_feedback(
        timeline, timestamp_sec=timestamp_sec, verdict=verdict, comment=comment  # type: ignore[arg-type]
    )
    store.save_feedback(row)
    click.echo(json.dumps(row.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
