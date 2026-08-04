"""EDL inspection — read-only report for regression freeze / debugging.

Does not modify files. Timing still comes only from the EDL on disk
(FFmpeg/PySceneDetect-derived windows); this module never invents cuts.

    python -m pipeline.inspect_edl work/<project>/edl_ai.json
    python run.py inspect-edl work/<project>/edl_baseline.json
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import click

from pipeline.edl import EDL


def inspect_edl(edl: EDL | dict[str, Any] | Path | str) -> dict[str, Any]:
    """Return a structured inspection report for one EDL."""
    if isinstance(edl, (str, Path)):
        path = Path(edl)
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed = EDL.model_validate(data)
        source_path = str(path)
    elif isinstance(edl, dict):
        parsed = EDL.model_validate(edl)
        data = edl
        source_path = None
    else:
        parsed = edl
        data = edl.model_dump()
        source_path = None

    sources = [c.source_file for c in parsed.timeline]
    segments = [c.segment_id for c in parsed.timeline]
    source_counts = Counter(sources)
    segment_counts = Counter(segments)

    unique_sources = sorted(source_counts)
    repeated_sources = sorted(s for s, n in source_counts.items() if n > 1)
    repeated_segments = sorted(s for s, n in segment_counts.items() if n > 1)

    clip_durations = [
        {
            "order": c.order,
            "segment_id": c.segment_id,
            "source_file": c.source_file,
            "duration_s": round(c.out_sec - c.in_sec, 3),
            "in_sec": c.in_sec,
            "out_sec": c.out_sec,
        }
        for c in parsed.timeline
    ]

    # Current EDL schema has generator but does not persist provider/model.
    model_info = {
        "generator": parsed.generator,
        "provider": data.get("provider") or data.get("model_provider") or None,
        "model": data.get("model") or data.get("model_id") or None,
        "analyzer_model": data.get("analyzer_model"),
        "director_model": data.get("director_model"),
        "evaluator_model": data.get("evaluator_model"),
        "note": (
            "EDL does not currently record provider/model; "
            "generator is baseline|ai only."
            if not (data.get("model") or data.get("provider"))
            else None
        ),
    }

    total_s = sum(c.out_sec - c.in_sec for c in parsed.timeline)
    if parsed.signature.enabled:
        total_s += parsed.signature.duration

    # Current product only ships one look; name it explicitly for inspect output.
    style_preset = (
        f"y2k_letterbox_{parsed.frame.aspect.replace(':', 'x')}"
        f"+lut:{parsed.aesthetic.lut}"
    )

    return {
        "path": source_path,
        "project": parsed.project,
        "version": parsed.version,
        "unique_source_count": len(unique_sources),
        "unique_sources": unique_sources,
        "repeated_source_count": len(repeated_sources),
        "repeated_sources": {
            s: source_counts[s] for s in repeated_sources
        },
        "repeated_segment_count": len(repeated_segments),
        "repeated_segments": {
            s: segment_counts[s] for s in repeated_segments
        },
        "timeline_clip_count": len(parsed.timeline),
        "total_duration_s": round(total_s, 3),
        "clip_durations": clip_durations,
        "captions": [
            {
                "segment_id": c.segment_id,
                "text": c.text,
                "style": c.style,
                "position": c.position,
                "start_offset_sec": c.start_offset_sec,
                "end_offset_sec": c.end_offset_sec,
            }
            for c in parsed.captions
        ],
        "style_preset": style_preset,
        "style": {
            "preset": style_preset,
            "canvas": parsed.canvas.model_dump(),
            "frame": parsed.frame.model_dump(),
            "aesthetic": parsed.aesthetic.model_dump(),
        },
        "frame_dimensions": {
            "canvas_width": parsed.canvas.width,
            "canvas_height": parsed.canvas.height,
            "frame_aspect": parsed.frame.aspect,
            "frame_width": parsed.frame.width,
            "frame_height": parsed.frame.height,
            "y_offset": parsed.frame.y_offset,
        },
        "generator_and_model": model_info,
        "audio": parsed.audio.model_dump(),
        "signature": parsed.signature.model_dump(),
    }


def format_report(report: dict[str, Any]) -> str:
    """Human-readable multi-line report."""
    lines: list[str] = []
    if report.get("path"):
        lines.append(f"path: {report['path']}")
    lines.append(f"project: {report['project']}  version: {report['version']}")
    gm = report["generator_and_model"]
    lines.append(
        f"generator: {gm.get('generator')}  "
        f"provider: {gm.get('provider') or '-'}  "
        f"model: {gm.get('model') or '-'}"
    )
    if gm.get("note"):
        lines.append(f"  ({gm['note']})")

    lines.append(f"style_preset: {report.get('style_preset')}")
    fd = report["frame_dimensions"]
    lines.append(
        f"canvas: {fd['canvas_width']}x{fd['canvas_height']}  "
        f"frame: {fd['frame_aspect']} {fd['frame_width']}x{fd['frame_height']} "
        f"y_offset={fd['y_offset']}"
    )
    lines.append(f"total_duration_s: {report['total_duration_s']}")
    lines.append(
        f"sources: {report['unique_source_count']} unique, "
        f"{report['repeated_source_count']} repeated"
    )
    if report["repeated_sources"]:
        for src, n in report["repeated_sources"].items():
            lines.append(f"  repeat source x{n}: {src}")
    lines.append(
        f"segments: {report['timeline_clip_count']} clips, "
        f"{report['repeated_segment_count']} segment_ids reused"
    )
    if report["repeated_segments"]:
        for seg, n in report["repeated_segments"].items():
            lines.append(f"  repeat segment x{n}: {seg}")

    lines.append("clip_durations:")
    for clip in report["clip_durations"]:
        lines.append(
            f"  #{clip['order']} {clip['segment_id']} "
            f"{clip['duration_s']:.3f}s "
            f"[{clip['in_sec']}-{clip['out_sec']}] {clip['source_file']}"
        )

    lines.append(f"captions ({len(report['captions'])}):")
    if not report["captions"]:
        lines.append("  (none)")
    for cap in report["captions"]:
        lines.append(
            f"  [{cap['style']}] {cap['segment_id']}: {cap['text']!r} "
            f"@{cap['start_offset_sec']}-{cap['end_offset_sec']}"
        )

    aes = report["style"]["aesthetic"]
    lines.append(
        f"aesthetic: lut={aes['lut']} grain={aes['grain']} bloom={aes['bloom']}"
    )
    return "\n".join(lines)


@click.command()
@click.argument("edl_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, default=False, help="emit JSON report")
def main(edl_path: Path, as_json: bool) -> None:
    report = inspect_edl(edl_path)
    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        click.echo(format_report(report))


if __name__ == "__main__":
    main()
