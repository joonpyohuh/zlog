"""Structured tagging — attaches shot/subject/mood/keep_score tags to every
passing candidate frame, ahead of select_ai.py.

Reads:  work/<project>/candidates.json
        work/<project>/contact_sheet_manifest.json
        work/<project>/contact_sheets/*.jpg
Writes: work/<project>/tags.json
        work/<project>/candidates.json (rewritten in place: each passing
        candidate in "candidates" gets a `tags` field merged in)

One tool-use call per contact sheet (never per frame — a sheet holds many
candidates, and batching keeps this cheap), fired concurrently via
asyncio. Each call is forced through the submit_tags tool, never a
"please output JSON" prompt. select_ai.py reads the merged tags back off
candidates.json and passes them to the model as plain text alongside the
sheet images — tagging never invents a timecode or a segment_id, it only
describes frames that already exist.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import anthropic
import click
from dotenv import load_dotenv

from pipeline.edl import CandidatesFile, CandidateScene, Tags

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024

SUBMIT_TAGS_TOOL = {
    "name": "submit_tags",
    "description": "Submit structured tags for every numbered frame on this contact sheet.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "shot": {"type": "string", "enum": ["wide", "medium", "close"]},
                        "subject": {
                            "type": "string",
                            "enum": ["person", "food", "landscape", "object", "text"],
                        },
                        "face_visible": {"type": "boolean"},
                        "mood": {"type": "string", "enum": ["bright", "calm", "lively", "moody"]},
                        "keep_score": {"type": "integer", "minimum": 1, "maximum": 5},
                    },
                    "required": ["index", "shot", "subject", "face_visible", "mood", "keep_score"],
                },
            }
        },
        "required": ["tags"],
    },
}


def _encode_image(path: Path) -> dict:
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


async def _tag_sheet(
    client: anthropic.AsyncAnthropic,
    sheet_path: Path,
    entries: list[tuple[int, str]],
) -> dict[str, dict]:
    """One batched call for one sheet. Returns {segment_id: tag_dict}."""
    instruction = (
        f"이 컨택트 시트에는 번호(1~{len(entries)})가 매겨진 프레임이 {len(entries)}개 있다.\n"
        f"각 프레임을 보고 shot/subject/face_visible/mood/keep_score를 판단해서 "
        f"submit_tags 도구로 전부 제출해라. index는 시트에 보이는 번호와 정확히 일치해야 한다."
    )
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        tools=[SUBMIT_TAGS_TOOL],
        tool_choice={"type": "tool", "name": "submit_tags"},
        messages=[{"role": "user", "content": [_encode_image(sheet_path), {"type": "text", "text": instruction}]}],
    )

    tags_by_index: dict[int, dict] = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_tags":
            for t in block.input["tags"]:
                tags_by_index[t["index"]] = t
            break
    else:
        raise RuntimeError(f"model response for {sheet_path.name} did not include a submit_tags tool call")

    index_to_segment = dict(entries)
    result: dict[str, dict] = {}
    for index, segment_id in index_to_segment.items():
        tag = tags_by_index.get(index)
        if tag is None:
            continue  # model skipped this index — leave it untagged rather than guessing
        result[segment_id] = {k: v for k, v in tag.items() if k != "index"}
    return result


async def _tag_all_sheets(
    client: anthropic.AsyncAnthropic,
    sheets_dir: Path,
    sheets: dict[str, list[tuple[int, str]]],
) -> dict[str, dict]:
    results = await asyncio.gather(
        *(_tag_sheet(client, sheets_dir / sheet_file, entries) for sheet_file, entries in sheets.items())
    )
    merged: dict[str, dict] = {}
    for r in results:
        merged.update(r)
    return merged


def tag_candidates(work_dir: Path, project: str) -> Path:
    """Tag every passing candidate via batched-per-sheet Claude calls, write
    tags.json, and merge the result back into candidates.json.
    """
    project_dir = work_dir / project
    candidates_file = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    candidate_ids = {c.segment_id for c in candidates_file.candidates}
    if not candidate_ids:
        raise ValueError(f"no passing candidates in {project_dir / 'candidates.json'}")

    manifest = json.loads((project_dir / "contact_sheet_manifest.json").read_text(encoding="utf-8"))["manifest"]

    sheets: dict[str, list[tuple[int, str]]] = {}
    for segment_id, entry in manifest.items():
        if segment_id not in candidate_ids:
            continue
        sheets.setdefault(entry["sheet"], []).append((entry["index"], segment_id))
    if not sheets:
        raise ValueError(
            f"no overlap between {project_dir / 'contact_sheet_manifest.json'} and "
            f"candidates.json's passing segment_ids"
        )

    load_dotenv()
    client = anthropic.AsyncAnthropic()
    sheets_dir = project_dir / "contact_sheets"
    tags_by_segment = asyncio.run(_tag_all_sheets(client, sheets_dir, sheets))

    tags_path = project_dir / "tags.json"
    tags_path.write_text(
        json.dumps({"project": project, "tags": tags_by_segment}, indent=2), encoding="utf-8"
    )

    updated: list[CandidateScene] = []
    for c in candidates_file.candidates:
        tag = tags_by_segment.get(c.segment_id)
        data = c.model_dump()
        if tag:
            data["tags"] = Tags(**tag).model_dump()
        updated.append(CandidateScene(**data))
    candidates_file.candidates = updated
    (project_dir / "candidates.json").write_text(candidates_file.model_dump_json(indent=2), encoding="utf-8")

    click.echo(f"tagged {len(tags_by_segment)}/{len(candidate_ids)} passing candidates")
    return tags_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
def main(work_dir: Path, project: str) -> None:
    out = tag_candidates(work_dir, project)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
