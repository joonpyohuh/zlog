"""Contact sheet generation — the only thing the AI selector is allowed to see.

Reads:  work/<project>/candidates.json
        work/<project>/frames/<segment_id>.jpg
Writes: work/<project>/contact_sheets/sheet_*.jpg
        work/<project>/contact_sheet_manifest.json
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import click
from PIL import Image, ImageDraw, ImageFont

from pipeline.edl import CandidatesFile

CELLS_PER_SHEET = 12
COLS = 3
CELL = 280
LABEL_H = 56
MARGIN = 12
BG = (12, 12, 12)
FG = (240, 240, 240)
MUTED = (160, 160, 160)


def build_contact_sheets(work_dir: Path, project: str) -> Path:
    """Lay out passing-candidate frames into labeled grids + manifest."""
    project_dir = work_dir / project
    candidates_file = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    pool = candidates_file.candidates
    if not pool:
        raise ValueError(f"no passing candidates in {project_dir / 'candidates.json'}")

    sheets_dir = project_dir / "contact_sheets"
    if sheets_dir.exists():
        for old in sheets_dir.glob("sheet_*.jpg"):
            old.unlink()
    sheets_dir.mkdir(parents=True, exist_ok=True)

    try:
        font_big = ImageFont.truetype("arial.ttf", 36)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font_big = ImageFont.load_default()
        font_small = font_big

    manifest: dict[str, dict] = {}
    n_sheets = max(1, math.ceil(len(pool) / CELLS_PER_SHEET))

    for sheet_i in range(n_sheets):
        chunk = pool[sheet_i * CELLS_PER_SHEET : (sheet_i + 1) * CELLS_PER_SHEET]
        rows = math.ceil(len(chunk) / COLS)
        sheet_name = f"sheet_{sheet_i + 1:03d}.jpg"
        cell_w = CELL + MARGIN
        cell_h = CELL + LABEL_H + MARGIN
        w = COLS * cell_w + MARGIN
        h = rows * cell_h + MARGIN
        canvas = Image.new("RGB", (w, h), BG)
        draw = ImageDraw.Draw(canvas)

        for local_i, cand in enumerate(chunk):
            index = local_i + 1  # 1-based per sheet
            col, row = local_i % COLS, local_i // COLS
            x = MARGIN + col * cell_w
            y = MARGIN + row * cell_h

            frame_path = project_dir / cand.frame_path
            thumb = Image.open(frame_path).convert("RGB")
            thumb.thumbnail((CELL, CELL), Image.LANCZOS)
            px = x + (CELL - thumb.width) // 2
            py = y + (CELL - thumb.height) // 2
            canvas.paste(thumb, (px, py))

            draw.rectangle((x, y + CELL, x + CELL, y + CELL + LABEL_H), fill=(20, 20, 20))
            draw.text((x + 8, y + CELL + 4), str(index), fill=FG, font=font_big)
            draw.text((x + 8, y + CELL + 34), cand.segment_id, fill=MUTED, font=font_small)

            manifest[cand.segment_id] = {
                "sheet": sheet_name,
                "index": index,
                "row": row,
                "col": col,
            }

        canvas.save(sheets_dir / sheet_name, quality=90)

    out = project_dir / "contact_sheet_manifest.json"
    out.write_text(
        json.dumps({"project": project, "manifest": manifest}, indent=2),
        encoding="utf-8",
    )
    click.echo(f"wrote {len(manifest)} cells across {n_sheets} sheet(s) → {out}")
    return out


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
def main(work_dir: Path, project: str) -> None:
    out = build_contact_sheets(work_dir, project)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
