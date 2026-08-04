"""Claude-based segment selection.

Reads:  work/<project>/candidates.json
        work/<project>/contact_sheet_manifest.json
        work/<project>/contact_sheets/*.jpg
        assets/bgm/<track>.beats.json
Writes: work/<project>/edl_ai.json  (EDL, generator="ai"; see edl.py)

The model sees contact sheet images (base64, one user message) plus —
if pipeline/tag.py has already run — each candidate's structured tags as
plain text, plus — if taste/taste_profile.json or taste/examples.jsonl
exist — pipeline/taste.py's founder-taste block (rules as cached system
text, past keep/drop example thumbnails attached in the user turn). It
picks segment_id + order + role + reason + a short Korean caption from
among the segment_ids already printed on the sheets. It never receives a
video file and is never asked for a timestamp — caption *placement*
(offsets) is computed in pipeline/captions.py from the host clip. A tool (submit_selection) with a fixed
input_schema is the only way the model can answer; there is no "please
output JSON" prompt asking it nicely. Once picked, the beat-snap logic is
identical to select_baseline.py's (_cut_durations/_place_cut, imported
from there so the two selectors can never drift apart). The result goes
through the same edl.validate_edl() as the baseline; on failure we retry
once with the validation problems appended to the prompt.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic
import click
from dotenv import load_dotenv

from pipeline import taste
from pipeline.captions import build_captions_from_ai
from pipeline.edl import (
    EDL,
    Audio,
    CandidatesFile,
    TimelineClip,
    validate_edl,
)
from pipeline.select_baseline import (
    AUDIO_VOLUME,
    DEFAULT_AESTHETIC,
    DEFAULT_CANVAS,
    DEFAULT_FRAME,
    DEFAULT_SIGNATURE,
    EDL_VERSION,
    _beats_path,
    _cut_durations,
    _place_cut,
    assign_transitions,
    expand_timeline_to_target,
    extend_beat_grid,
)

MODEL = "claude-sonnet-4-5-20250929"
MAX_TOKENS = 2000
MAX_RETRIES = 1

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "select.md"

SUBMIT_SELECTION_TOOL = {
    "name": "submit_selection",
    "description": "Submit the chosen segment_ids, in final cut order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "selected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "segment_id": {"type": "string"},
                        "order": {"type": "integer"},
                        "role": {
                            "type": "string",
                            "enum": ["opening", "body", "peak", "closing"],
                        },
                        "reason": {"type": "string", "maxLength": 60},
                        "caption": {
                            "type": "string",
                            "maxLength": 24,
                            "description": (
                                "이 컷 위에 얹을 한국어 브이로그 자막 (24자 이내). "
                                "화면에 실제로 보이는 것에 맞는 말만. 자막이 필요 "
                                "없는 컷은 빈 문자열."
                            ),
                        },
                    },
                    "required": ["segment_id", "order", "role", "reason", "caption"],
                },
            }
        },
        "required": ["selected"],
    },
}


def _encode_image(path: Path) -> dict:
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _tags_summary(candidates_file: CandidatesFile, valid_segment_ids: set[str]) -> str | None:
    """tag.py's per-frame tags, rendered as plain text — the model gets
    these alongside the sheet images, never as a substitute for looking.
    """
    by_id = {c.segment_id: c for c in candidates_file.candidates}
    lines = []
    for segment_id in sorted(valid_segment_ids):
        tags = by_id[segment_id].tags
        if tags is None:
            continue
        lines.append(
            f"- {segment_id}: shot={tags.shot}, subject={tags.subject}, "
            f"face_visible={tags.face_visible}, mood={tags.mood}, keep_score={tags.keep_score}"
        )
    if not lines:
        return None
    return "pipeline/tag.py가 사전 분석한 태그 (참고용, 최종 판단은 이미지로 할 것):\n" + "\n".join(lines)


def _taste_example_blocks(taste_examples: list[taste.Example]) -> list[dict]:
    if not taste_examples:
        return []
    blocks: list[dict] = [
        {
            "type": "text",
            "text": "다음은 과거에 실제로 keep/drop 판단을 내린 예시 프레임들이다 (참고용, taste/examples.jsonl):",
        }
    ]
    for example in taste_examples:
        frame_path = taste.REPO_ROOT / example.frame_path
        if not frame_path.exists():
            continue
        blocks.append(_encode_image(frame_path))
        blocks.append({"type": "text", "text": f"[{example.decision.upper()}] {example.reason}"})
    return blocks


def _build_user_content(
    sheets_dir: Path,
    sheet_files: list[str],
    n_cuts: int,
    valid_segment_ids: set[str],
    feedback: list[str] | None,
    tags_text: str | None,
    taste_examples: list[taste.Example],
) -> list[dict]:
    content = _taste_example_blocks(taste_examples)
    content += [_encode_image(sheets_dir / f) for f in sheet_files]
    if tags_text:
        content.append({"type": "text", "text": tags_text})

    instruction = (
        f"이 프로젝트에는 총 {len(valid_segment_ids)}개의 후보 컷이 시트에 라벨로 붙어 있다.\n"
        f"정확히 {n_cuts}개를 골라 순서(1~{n_cuts})를 정해서 submit_selection 도구로 제출해라.\n"
        f"사용 가능한 segment_id: {', '.join(sorted(valid_segment_ids))}"
    )
    if feedback:
        instruction += "\n\n이전 제출은 검증에 실패했다. 아래 이유를 반드시 고쳐서 다시 제출해라:\n"
        instruction += "\n".join(f"- {p}" for p in feedback)

    content.append({"type": "text", "text": instruction})
    return content


def _call_claude(
    client: anthropic.Anthropic,
    system_prompt: str,
    taste_block: dict | None,
    sheets_dir: Path,
    sheet_files: list[str],
    n_cuts: int,
    valid_segment_ids: set[str],
    feedback: list[str] | None,
    tags_text: str | None,
    taste_examples: list[taste.Example],
) -> list[dict]:
    content = _build_user_content(
        sheets_dir, sheet_files, n_cuts, valid_segment_ids, feedback, tags_text, taste_examples
    )
    system: list[dict] = [{"type": "text", "text": system_prompt}]
    if taste_block:
        system.append(taste_block)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        tools=[SUBMIT_SELECTION_TOOL],
        tool_choice={"type": "tool", "name": "submit_selection"},
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_selection":
            return block.input["selected"]
    raise RuntimeError("model response did not include a submit_selection tool call")


def _build_edl(
    selected: list[dict],
    candidates_file: CandidatesFile,
    cut_durations: list[float],
    beat_times: list[float],
    project: str,
    bgm_track: Path,
    note: str | None = None,
    target_duration_s: float = 15.0,
    tempo_bpm: float = 120.0,
) -> tuple[EDL, list[str]]:
    by_id = {c.segment_id: c for c in candidates_file.candidates}
    problems: list[str] = []
    ordered = sorted(selected, key=lambda s: s["order"])

    timeline: list[TimelineClip] = []
    for i, sel in enumerate(ordered):
        segment_id = sel["segment_id"]
        candidate = by_id.get(segment_id)
        if candidate is None:
            problems.append(
                f"model chose segment_id={segment_id!r}, which is not a passing candidate"
            )
            continue
        cut_duration = cut_durations[min(i, len(cut_durations) - 1)]
        in_sec, out_sec = _place_cut(candidate, cut_duration, beat_times)
        timeline.append(
            TimelineClip(
                order=len(timeline) + 1,
                segment_id=segment_id,
                source_file=candidate.source_file,
                in_sec=in_sec,
                out_sec=out_sec,
            )
        )
    timeline = expand_timeline_to_target(
        timeline,
        candidates_file.candidates,
        beat_times,
        target_duration_s,
        tempo_bpm,
    )
    timeline = assign_transitions(timeline)

    edl = EDL(
        project=project,
        version=EDL_VERSION,
        generator="ai",
        canvas=DEFAULT_CANVAS,
        frame=DEFAULT_FRAME,
        aesthetic=DEFAULT_AESTHETIC,
        audio=Audio(bgm_id=bgm_track.stem, start_sec=0.0, volume=AUDIO_VOLUME),
        timeline=timeline,
        captions=build_captions_from_ai(timeline, ordered, by_id, note=note),
        signature=DEFAULT_SIGNATURE,
    )
    return edl, problems


def select_ai(
    work_dir: Path,
    project: str,
    bgm_track: Path,
    target_duration_s: float,
) -> Path:
    """Ask Claude to pick segment_ids from the contact sheets, snap the
    chosen segments' timecodes to the beat grid, and write edl_ai.json.
    """
    project_dir = work_dir / project
    candidates_file = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    if not candidates_file.candidates:
        raise ValueError(f"no passing candidates in {project_dir / 'candidates.json'}")

    manifest = json.loads((project_dir / "contact_sheet_manifest.json").read_text(encoding="utf-8"))
    seg_to_sheet: dict[str, dict] = manifest["manifest"]
    candidate_ids = {c.segment_id for c in candidates_file.candidates}
    valid_segment_ids = set(seg_to_sheet) & candidate_ids
    if not valid_segment_ids:
        raise ValueError(
            f"no overlap between {project_dir / 'contact_sheet_manifest.json'} and "
            f"candidates.json's passing segment_ids"
        )
    sheet_files = sorted({entry["sheet"] for entry in seg_to_sheet.values()})
    sheets_dir = project_dir / "contact_sheets"

    beats_data = json.loads(_beats_path(bgm_track).read_text(encoding="utf-8"))
    tempo_bpm = beats_data["tempo_bpm"]
    beat_times = extend_beat_grid(
        beats_data["beat_times"],
        tempo_bpm,
        max(c.end_sec for c in candidates_file.candidates),
    )
    cut_durations = _cut_durations(target_duration_s, tempo_bpm)
    n_cuts = min(len(cut_durations), len(valid_segment_ids))
    cut_durations = cut_durations[:n_cuts]

    tags_text = _tags_summary(candidates_file, valid_segment_ids)
    taste_block, taste_examples = taste.build_taste_block()

    load_dotenv()
    client = anthropic.Anthropic()
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    note_path = project_dir / "note.txt"
    note = note_path.read_text(encoding="utf-8").strip() if note_path.exists() else None

    edl: EDL | None = None
    problems: list[str] = []
    feedback: list[str] | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        selected = _call_claude(
            client, system_prompt, taste_block, sheets_dir, sheet_files, n_cuts,
            valid_segment_ids, feedback, tags_text, taste_examples,
        )
        edl, build_problems = _build_edl(
            selected,
            candidates_file,
            cut_durations,
            beat_times,
            project,
            bgm_track,
            note=note,
            target_duration_s=target_duration_s,
            tempo_bpm=tempo_bpm,
        )
        problems = build_problems + validate_edl(edl, candidates_file, beat_times, target_duration_s)

        if not problems:
            click.echo(f"edl_ai.json passed validation (attempt {attempt})")
            break

        click.echo(f"attempt {attempt} validation problems:")
        for p in problems:
            click.echo(f"  - {p}")
        feedback = problems

    if problems:
        click.echo("edl_ai.json still has validation problems after retry:")
        for p in problems:
            click.echo(f"  - {p}")
        raise ValueError(
            "AI selection failed validation: " + "; ".join(problems[:4])
        )

    out_path = project_dir / "edl_ai.json"
    out_path.write_text(edl.model_dump_json(indent=2), encoding="utf-8")
    return out_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--bgm-track", type=click.Path(path_type=Path), required=True)
@click.option("--target-duration-s", type=float, default=15.0)
def main(work_dir: Path, project: str, bgm_track: Path, target_duration_s: float) -> None:
    out = select_ai(work_dir, project, bgm_track, target_duration_s)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
