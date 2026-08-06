"""Batch updater — narrow the StyleProfile from logged picks.

    python -m pipeline.taste_loop.update_profile

Reads every resolved round, groups the winning axis values by axis, and pulls
that axis's range toward where the picks cluster. Statistics only; no model is
trained and nothing here looks at a video.

Rules that keep this honest:
- An axis with fewer than MIN_SAMPLES picks is left completely alone and
  reported as "데이터 부족". Narrowing on three data points would be noise
  dressed up as taste.
- "None of these" rounds count as data collected but contribute no winning
  value — they are excluded from the statistics, not treated as a vote.
- The new range never escapes the registry's allowed bounds and never shrinks
  below the axis's min_span, so the loop can keep producing four visibly
  different variants next round.
- The previous profile is never overwritten: a new version file is written
  and both before and after are printed.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import click
from pydantic import BaseModel

from pipeline.taste_loop.axes import REGISTRY, AxisRange, TasteAxis, spec_for
from pipeline.taste_loop.store import ComparisonRound, TasteStore, get_store
from pipeline.taste_loop.style_profile import (
    AxisNarrowing,
    StyleProfile,
    load_latest,
    save_new_version,
)

MIN_SAMPLES = 5
# How far the new range reaches either side of the winners' mean, as a
# multiple of their spread. Wide enough that the next round still explores.
SPREAD_MULTIPLIER = 1.5


class AxisOutcome(BaseModel):
    """What the updater decided for one axis."""

    axis: TasteAxis
    sample_size: int
    rejected_all_count: int
    winners: list[float]
    before: AxisRange
    after: AxisRange | None  # None = untouched (insufficient data)
    reason: str


def collect_winning_values(rounds: list[ComparisonRound]) -> dict[TasteAxis, list[float]]:
    """Winning axis values per axis, from resolved rounds that picked one."""
    by_axis: dict[TasteAxis, list[float]] = {axis: [] for axis in REGISTRY}
    for round_ in rounds:
        if not round_.resolved or round_.rejected_all:
            continue
        value = round_.winning_value()
        if value is None:
            continue
        by_axis.setdefault(round_.axis, []).append(value)
    return by_axis


def count_rejected_all(rounds: list[ComparisonRound]) -> dict[TasteAxis, int]:
    counts: dict[TasteAxis, int] = {axis: 0 for axis in REGISTRY}
    for round_ in rounds:
        if round_.resolved and round_.rejected_all:
            counts[round_.axis] = counts.get(round_.axis, 0) + 1
    return counts


def narrow_range(axis: TasteAxis, current: AxisRange, winners: list[float]) -> AxisRange:
    """Centre the range on the winners' mean, sized by their spread.

    Clamped to the registry's allowed bounds and widened back out if it would
    fall under the axis's min_span.
    """
    spec = spec_for(axis)
    mean = statistics.fmean(winners)
    spread = statistics.stdev(winners) if len(winners) > 1 else 0.0

    half = max(spread * SPREAD_MULTIPLIER, spec.min_span / 2.0)
    low = mean - half
    high = mean + half

    # Never widen: this step is called "narrowing" and should only ever shrink.
    low = max(low, current.low)
    high = min(high, current.high)
    if high < low:
        low = high = min(max(mean, current.low), current.high)

    # Restore min_span if clamping squeezed it, staying inside `current`.
    if high - low < spec.min_span:
        centre = (low + high) / 2.0
        low = centre - spec.min_span / 2.0
        high = centre + spec.min_span / 2.0
        if low < current.low:
            low, high = current.low, min(current.high, current.low + spec.min_span)
        if high > current.high:
            high, low = current.high, max(current.low, current.high - spec.min_span)

    low = max(spec.allowed.low, round(low, 4))
    high = min(spec.allowed.high, round(high, 4))
    low = min(low, high)
    return AxisRange(low=low, high=high)


def compute_outcomes(
    profile: StyleProfile, rounds: list[ComparisonRound], *, min_samples: int = MIN_SAMPLES
) -> list[AxisOutcome]:
    winners_by_axis = collect_winning_values(rounds)
    rejected_by_axis = count_rejected_all(rounds)

    outcomes: list[AxisOutcome] = []
    for axis in REGISTRY:
        winners = winners_by_axis.get(axis, [])
        before = profile.range_for(axis)
        if len(winners) < min_samples:
            outcomes.append(
                AxisOutcome(
                    axis=axis,
                    sample_size=len(winners),
                    rejected_all_count=rejected_by_axis.get(axis, 0),
                    winners=winners,
                    before=before,
                    after=None,
                    reason=f"데이터 부족 ({len(winners)}/{min_samples})",
                )
            )
            continue
        after = narrow_range(axis, before, winners)
        outcomes.append(
            AxisOutcome(
                axis=axis,
                sample_size=len(winners),
                rejected_all_count=rejected_by_axis.get(axis, 0),
                winners=winners,
                before=before,
                after=after,
                reason="narrowed",
            )
        )
    return outcomes


def apply_outcomes(
    profile: StyleProfile,
    outcomes: list[AxisOutcome],
    *,
    profile_dir: Path | None = None,
) -> tuple[StyleProfile, Path] | None:
    """Write a new profile version if anything actually changed."""
    changed = [o for o in outcomes if o.after is not None and o.after != o.before]
    if not changed:
        return None

    ranges = {axis: rng.model_copy() for axis, rng in profile.ranges.items()}
    narrowings: list[AxisNarrowing] = []
    for outcome in changed:
        assert outcome.after is not None
        ranges[outcome.axis] = outcome.after
        narrowings.append(
            AxisNarrowing(
                axis=outcome.axis,
                before=outcome.before,
                after=outcome.after,
                sample_size=outcome.sample_size,
                winner_mean=round(statistics.fmean(outcome.winners), 4),
                winner_stdev=round(
                    statistics.stdev(outcome.winners) if len(outcome.winners) > 1 else 0.0, 4
                ),
            )
        )
    axes_changed = ", ".join(o.axis.value for o in changed)
    return save_new_version(
        profile,
        ranges,
        narrowings,
        profile_dir=profile_dir,
        note=f"narrowed from picks: {axes_changed}",
    )


def run_update(
    store: TasteStore,
    *,
    profile_dir: Path | None = None,
    min_samples: int = MIN_SAMPLES,
    dry_run: bool = False,
    echo=click.echo,
) -> list[AxisOutcome]:
    profile = load_latest(profile_dir)
    rounds = store.list_rounds(limit=10_000)
    resolved = [r for r in rounds if r.resolved]

    echo(f"StyleProfile v{profile.version} · rounds logged: {len(rounds)} (resolved: {len(resolved)})")
    echo("")

    outcomes = compute_outcomes(profile, resolved, min_samples=min_samples)

    for outcome in outcomes:
        spec = spec_for(outcome.axis)
        echo(f"[{outcome.axis.value}]  ({spec.unit})")
        echo(f"  picks: {outcome.sample_size}   넷 다 별로: {outcome.rejected_all_count}")
        if outcome.after is None:
            echo(f"  before: [{outcome.before.low}, {outcome.before.high}]")
            echo(f"  after : (변경 없음) — {outcome.reason}")
        else:
            mean = statistics.fmean(outcome.winners)
            stdev = statistics.stdev(outcome.winners) if len(outcome.winners) > 1 else 0.0
            echo(f"  winners: mean={mean:.4f} stdev={stdev:.4f} values={outcome.winners}")
            echo(f"  before: [{outcome.before.low}, {outcome.before.high}]"
                 f"  (span {outcome.before.span:.4f})")
            echo(f"  after : [{outcome.after.low}, {outcome.after.high}]"
                 f"  (span {outcome.after.span:.4f})")
        echo("")

    if dry_run:
        echo("dry-run: no profile written")
        return outcomes

    written = apply_outcomes(profile, outcomes, profile_dir=profile_dir)
    if written is None:
        echo("변경된 축이 없어 새 StyleProfile 버전을 만들지 않았다.")
    else:
        new_profile, path = written
        echo(f"wrote StyleProfile v{new_profile.version} -> {path}")
        echo(f"(이전 버전 v{profile.version}은 그대로 남아 있다)")
    return outcomes


@click.command()
@click.option("--store", "backend", default=None, help="local | supabase (default: env or local)")
@click.option("--profile-dir", type=click.Path(path_type=Path), default=None)
@click.option("--min-samples", type=int, default=MIN_SAMPLES, show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="print only, write nothing")
def main(backend: str | None, profile_dir: Path | None, min_samples: int, dry_run: bool) -> None:
    """Narrow the StyleProfile from logged comparison picks."""
    run_update(
        get_store(backend),
        profile_dir=profile_dir,
        min_samples=min_samples,
        dry_run=dry_run,
    )


if __name__ == "__main__":
    main()
