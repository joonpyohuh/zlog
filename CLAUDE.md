# zlog — AI 영상 편집 파이프라인 (프로토타입)

## 제품 개요

사용자의 사진/영상 원본을 넣으면 15~60초짜리 브이로그 mp4가 나온다.

- **비주얼 컨셉**: 2000년대 CCD 캠코더 감성의 Y2K 레트로 룩 (쿨톤, 4:3 레터박스)
- **엔딩**: 영상 마지막 2초, 검은 화면 + 흰 산세리프로 "directed by zlog" 크레딧

지금 단계는 **로컬 CLI 스크립트만** 만든다. 웹앱은 만들지 않는다.

> 제품 비전·수익 모델·로드맵 등 전체 맥락은 `zlog 기획서임.pdf` 참고. 이 저장소는
> 로드맵상 "Founder Taste Lab" 단계에서 창업자 본인이 쓸 편집 파이프라인의 뼈대다.

## 아키텍처 대원칙 (반드시 지킬 것)

1. **AI 모델은 영상 파일을 절대 직접 다루지 않는다.** 프레임 이미지만 본다.
2. **타임코드는 100% FFmpeg/PySceneDetect가 만든다.** LLM이 타임스탬프를 생성하는
   코드는 절대 작성하지 않는다. LLM은 미리 계산된 후보 세그먼트 중에서 `segment_id`를
   "고르기"만 한다.
3. **컷 전환 지점은 BGM 비트 그리드에 스냅한다.** 리듬을 LLM이 정하지 않는다.
4. **색보정, 크롭, 크레딧은 전부 결정론적 렌더 단계에서 처리한다.**
5. **각 단계는 독립 실행 가능해야 하고, 중간 산출물을 JSON/파일로 디스크에 남긴다.**
   앞 단계를 다시 돌리지 않고 뒷 단계만 반복 실행할 수 있어야 한다.

이 5가지는 코드 리뷰의 최우선 체크리스트다. 새 코드가 위 원칙 중 하나라도 위반하면
(예: LLM 응답에서 초 단위 타임스탬프를 파싱해서 쓴다거나, select_ai.py가 비디오 파일
경로를 모델 프롬프트에 넣는다거나) 반드시 지적하고 고친다.

## 디렉토리 구조

```
zlog/
  run.py                  # 오케스트레이터 CLI (click)
  pipeline/
    split.py              # 씬 분할 + 프레임 추출
    filter.py              # 품질 필터
    sheet.py               # 컨택트 시트 생성
    beats.py                # BGM 비트 그리드
    select_baseline.py    # AI 없는 휴리스틱 선택
    select_ai.py           # Claude API 선택 (나중)
    edl.py                  # EDL 스키마 정의 + 검증
  render/                  # Remotion 프로젝트 (나중)
  work/                    # 중간 산출물 (gitignore, run_id별 하위 폴더)
  footage/                 # 원본 (gitignore)
  assets/
    bgm/                   # BGM + 비트 그리드 json
    luts/                  # .cube 파일
  evals/                   # 골든 데이터셋 (나중)
```

`work/<project>/`가 한 번의 파이프라인 실행 단위다. `project`는 `footage/<project>/`
디렉토리 이름에서 그대로 가져온다 (예: `footage/trip_01/` → `work/trip_01/`). 같은
원본으로 여러 번 실험해도 서로 덮어쓰지 않도록 project로 분리한다.

## 기술 스택

- Python 3.11, `uv` (또는 venv) — 의존성은 `pyproject.toml`
- PySceneDetect, opencv-python, Pillow, imagehash, librosa, ffmpeg-python
- 렌더: Remotion (TypeScript, `render/`, 나중에 생성)
- 색보정: ffmpeg `lut3d` 필터 + `.cube` LUT (`assets/luts/`)

## 파이프라인 단계별 입출력 계약

모든 경로는 저장소 루트 기준. `<project>`는 `footage/<project>/` 디렉토리 이름에서
그대로 가져오는 식별자.

| 단계 | 모듈 | 읽기 (input) | 쓰기 (output) |
|---|---|---|---|
| 씬 분할 | `pipeline/split.py` | `footage/<project>/*.mp4\|*.mov` | `work/<project>/segments.json`, `work/<project>/frames/<segment_id>.jpg` |
| 품질 필터 | `pipeline/filter.py` | `work/<project>/segments.json`, `work/<project>/frames/**` | `work/<project>/segments_filtered.json` |
| 컨택트 시트 | `pipeline/sheet.py` | `work/<project>/segments_filtered.json`, `work/<project>/frames/**` | `work/<project>/contact_sheets/sheet_*.jpg`, `work/<project>/contact_sheet_manifest.json` |
| 비트 그리드 | `pipeline/beats.py` | `assets/bgm/<track>.mp3` | `assets/bgm/<track>.beats.json` |
| 휴리스틱 선택 | `pipeline/select_baseline.py` | `work/<project>/segments_filtered.json`, `assets/bgm/<track>.beats.json` | `work/<project>/edl.json` (`created_by: "baseline"`) |
| AI 선택 (나중) | `pipeline/select_ai.py` | `work/<project>/contact_sheet_manifest.json`, `work/<project>/contact_sheets/*.jpg`, `work/<project>/segments_filtered.json`, `assets/bgm/<track>.beats.json` | `work/<project>/edl.json` (`created_by: "ai"`) |
| EDL 스키마/검증 | `pipeline/edl.py` | (다른 단계가 호출) | (다른 단계가 호출) |
| 렌더 (나중) | `render/` (Remotion) | `work/<project>/edl.json`, `assets/bgm/**`, `assets/luts/**` | `work/<project>/output.mp4` |

`select_baseline.py`와 `select_ai.py`는 **동일한 `edl.json` 스키마**를 출력해야 한다
(`pipeline/edl.py`의 `EDL`/`EDLClip`). 렌더 단계는 어느 쪽이 만든 EDL이든 구분 없이
소비할 수 있어야 한다.

## EDL 스키마 요약 (`pipeline/edl.py`)

- `Scene` — `split.py`가 쓰는 원시 세그먼트 (segment_id, source_file, start_sec, end_sec, duration, frame_path)
- `SegmentsFile` — `segments.json`/`segments_filtered.json` 파일 전체 (project, segments: list[Scene])
- `FilteredScene(Scene)` — `filter.py`가 품질 점수/keep 플래그를 덧붙인 것
- `EDLClip` — 최종 컷 하나 (segment_id, in_tc/out_tc, beat_snap_in/out, order)
- `EDL` — `project`, `bgm_track`, `target_duration_s`, `created_by`, `clips: list[EDLClip]`

`EDLClip.segment_id`는 반드시 `segments_filtered.json`에 있는 `segment_id`를 참조해야
하고, `in_tc`/`out_tc`는 그 세그먼트의 `[start_sec, end_sec]` 범위 안이어야 한다 — 이
불변식이 원칙 2번(타임코드는 FFmpeg/PySceneDetect 산출물만)을 코드 레벨에서 강제하는
지점이다.

## 실행 방법 (스켈레톤 상태)

```bash
uv sync
```

각 단계는 독립 실행 가능하다 (원칙 5). `project`는 `footage/<project>/` 디렉토리
이름에서 가져온다:

```bash
python -m pipeline.split footage/trip_01
python -m pipeline.filter --project trip_01
python -m pipeline.sheet --project trip_01
python -m pipeline.beats --track assets/bgm/track1.mp3
python -m pipeline.select_baseline --project trip_01 --bgm-track assets/bgm/track1.mp3
```

또는 오케스트레이터로 순서대로:

```bash
python run.py full footage/trip_01 --bgm-track assets/bgm/track1.mp3
```

`split.py`는 구현되어 있다 (PySceneDetect ContentDetector, 0.8~8초 길이 규칙, 중간
프레임 썸네일). 나머지 단계는 아직 `NotImplementedError`를 던지는 스켈레톤이다.

## 지금 하지 말 것

- 웹앱/API 서버를 만들지 않는다. 지금은 로컬 CLI뿐이다.
- `render/` (Remotion), `pipeline/filter.py`, `pipeline/sheet.py`, `pipeline/beats.py`,
  `pipeline/select_baseline.py`, `pipeline/select_ai.py`, `evals/`는 스켈레톤만 존재
  (`pipeline/split.py`만 구현됨) — 지시 없이 실제 로직을 채우지 않는다.
- 위 아키텍처 대원칙 5가지를 우회하는 "임시 방편"을 추가하지 않는다 (예: LLM이 프레임
  타임스탬프를 텍스트로 추정해서 쓰는 fallback 등).
