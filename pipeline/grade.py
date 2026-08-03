"""Color grading — deterministic ffmpeg post-process, no LLM involved.

Reads:  work/<project>/render.mp4         (Remotion's un-graded output)
        assets/luts/<lut>.cube            (lut named in the EDL's aesthetic.lut)
Writes: work/<project>/final.mp4

This is the second half of architecture principle 4 (color correction is
handled in a deterministic stage): Remotion (render/) owns cuts, cropping,
and the credit; this module owns the CCD-cool LUT pass via ffmpeg's lut3d
filter. Grain/bloom (also listed on EDL.aesthetic) aren't applied yet —
no filter design has been specified for them.
"""

from __future__ import annotations

from pathlib import Path

import click
import ffmpeg


def _lut3d_path_arg(lut_path: Path) -> str:
    """Normalize to forward slashes for ffmpeg's filtergraph parser.

    ffmpeg-python already escapes ':' in filter option values (needed for
    Windows drive letters like C:\\...) — do not double-escape it here.
    """
    return lut_path.as_posix()


def apply_lut(input_path: Path, lut_path: Path, output_path: Path) -> Path:
    """Run ffmpeg's lut3d filter over input_path using lut_path and write
    output_path. Audio is copied through untouched.
    """
    if not lut_path.exists():
        raise FileNotFoundError(f"LUT not found: {lut_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    src = ffmpeg.input(str(input_path))
    video = src.video.filter("lut3d", file=_lut3d_path_arg(lut_path))
    (
        ffmpeg.output(video, src.audio, str(output_path), acodec="copy", vcodec="libx264", crf=18)
        .overwrite_output()
        .run(quiet=True)
    )
    return output_path


@click.command()
@click.option("--input", "input_path", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--lut", "lut_path", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "output_path", type=click.Path(path_type=Path), required=True)
def main(input_path: Path, lut_path: Path, output_path: Path) -> None:
    out = apply_lut(input_path, lut_path, output_path)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
