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
  run.py                  # 오케스트레이터 CLI (click): pipeline(기본)/review 서브커맨드
  pipeline/
    split.py              # 씬 분할 + 프레임 추출
    filter.py              # 품질 필터
    sheet.py               # 컨택트 시트 생성 (나중)
    beats.py                # BGM 비트 그리드 (나중)
    tag.py                   # 후보 프레임 구조화 태깅 (Claude, select_ai 이전 단계)
    taste.py                 # taste_profile.json + examples.jsonl -> 프롬프트 블록
    select_baseline.py    # AI 없는 휴리스틱 선택
    select_ai.py           # Claude API 선택
    captions.py             # 자막 텍스트+배치 — LLM은 텍스트만 쓰고, 오프셋 계산은 전부 여기서
    edl.py                  # EDL 스키마 정의 + 검증
    grade.py                # ffmpeg LUT 색보정 (render 다음 단계)
  render/                  # Remotion 프로젝트 — EDL을 읽어 mp4로 렌더
    src/                   # ZlogFilm/Clip/Caption/CamcorderOverlay/FilmLook/EndingCredit, Root.tsx
    resolve-props.mjs       # EDL의 source_file/bgm_id를 실제 경로로 해석 (플레인 Node)
    public/                 # resolve-props.mjs가 매 렌더마다 채우는 스테이징 폴더 (gitignore)
  prompts/
    select.md               # select_ai.py 시스템 프롬프트 (자주 수정됨)
  taste/
    taste_profile.json       # 명시적 편집 취향 (직접 수정)
    examples.jsonl            # 실제 keep/drop 판단 기록 (`run.py review`가 append)
    examples/                 # examples.jsonl이 가리키는 프레임 썸네일
  work/                    # 중간 산출물 (gitignore, project별 하위 폴더)
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
- `select_ai.py`: `anthropic` SDK (tool use), `python-dotenv`로 `.env`의
  `ANTHROPIC_API_KEY` 로드. **API 키는 절대 코드/문서/커밋/채팅에 하드코딩하지
  않는다** — `.env`는 gitignore 처리되어 있고, `.env.example`만 커밋한다.
- 렌더: Remotion (TypeScript, `render/`, Node 18+, `npm install` 필요)
- 색보정: ffmpeg `lut3d` 필터 + `.cube` LUT (`assets/luts/`), `pipeline/grade.py`

## 파이프라인 단계별 입출력 계약

모든 경로는 저장소 루트 기준. `<project>`는 `footage/<project>/` 디렉토리 이름에서
그대로 가져오는 식별자.

| 단계 | 모듈 | 읽기 (input) | 쓰기 (output) |
|---|---|---|---|
| 씬 분할 | `pipeline/split.py` | `footage/<project>/*.mp4\|*.mov` | `work/<project>/segments.json`, `work/<project>/frames/<segment_id>.jpg` |
| 품질 필터 | `pipeline/filter.py` | `work/<project>/segments.json`, `work/<project>/frames/**` | `work/<project>/candidates.json`, `work/<project>/rejected_contact.jpg` |
| 컨택트 시트 (나중) | `pipeline/sheet.py` | `work/<project>/candidates.json`, `work/<project>/frames/**` | `work/<project>/contact_sheets/sheet_*.jpg`, `work/<project>/contact_sheet_manifest.json` |
| 비트 그리드 (나중) | `pipeline/beats.py` | `assets/bgm/<track>.mp3` | `assets/bgm/<track>.beats.json` |
| 구조화 태깅 | `pipeline/tag.py` | `work/<project>/candidates.json`, `work/<project>/contact_sheet_manifest.json`, `work/<project>/contact_sheets/*.jpg` | `work/<project>/tags.json`, `work/<project>/candidates.json` (제자리 갱신 — `candidates` 배열에 `tags` 병합) |
| 휴리스틱 선택 | `pipeline/select_baseline.py` | `work/<project>/candidates.json`, `assets/bgm/<track>.beats.json` | `work/<project>/edl_baseline.json` (`generator: "baseline"`) |
| AI 선택 | `pipeline/select_ai.py` | `work/<project>/contact_sheet_manifest.json`, `work/<project>/contact_sheets/*.jpg`, `work/<project>/candidates.json` (tag.py가 돌았다면 `tags` 포함), `assets/bgm/<track>.beats.json`, `prompts/select.md` | `work/<project>/edl_ai.json` (`generator: "ai"`) |
| EDL 스키마/검증 | `pipeline/edl.py` | (다른 단계가 호출) | (다른 단계가 호출) |
| 렌더 | `render/` (Remotion) | `work/<project>/edl_baseline.json` 또는 `edl_ai.json`, `footage/<project>/*`, `assets/bgm/**` | `work/<project>/render.mp4` |
| 색보정 | `pipeline/grade.py` | `work/<project>/render.mp4`, `assets/luts/<lut>.cube` | `work/<project>/final.mp4` |

`select_baseline.py`와 `select_ai.py`는 파일명(`edl_baseline.json` / `edl_ai.json`)만
다를 뿐 **동일한 EDL 스키마**를 출력해야 한다 (`pipeline/edl.py`의 `EDL`), 그리고
둘 다 비트 스냅 로직(`select_baseline._cut_durations`/`_place_cut`)을 그대로
가져다 쓴다 — select_ai.py가 자기만의 스냅 로직을 새로 만들면 두 선택기의 결과를
비교할 수 없게 되므로 금지. 렌더 단계는 어느 쪽이 만든 EDL이든 구분 없이 소비한다.

`filter.py`가 쓰는 `candidates.json`은 `candidates`(verdict가 `pass`인 것)와
`rejected`(그 외 전부, 사유 포함) 두 배열을 담는다 — 다운스트림(`sheet.py`,
`select_baseline.py`, `select_ai.py`)은 항상 `candidates` 배열만 참조한다.

`sheet.py`가 쓰는 `contact_sheet_manifest.json`은 `{project, manifest:
{<segment_id>: {sheet, index, row, col}}}` 형태다. `index`는 시트별로 1부터
다시 매기는 번호이고, 시트 이미지에는 반드시 이 번호와 segment_id가 함께 라벨로
보여야 한다 — `tag.py`는 번호로, `select_ai.py`는 segment_id로 참조한다. 라벨이
없으면 모델이 근거 없이 값을 지어낼 위험이 생긴다.

`tag.py`는 시트 한 장당 tool-use 호출 한 번(프레임 하나씩 부르지 않는다)을
`asyncio.gather`로 동시에 돌린다. 결과는 `candidates.json`의 `candidates`
배열에 `tags` 필드로 병합되고, `select_ai.py`는 이 태그를 시트 이미지와
함께 텍스트로만 참고 자료로 건넨다 — 태그가 최종 판단을 대신하지 않는다.

`taste.py`는 `taste/taste_profile.json`(명시적 규칙)과 `taste/examples.jsonl`
(과거 keep/drop 판단, keep 10개/drop 10개 랜덤 샘플)을 읽어 `select_ai.py`의
시스템 프롬프트에 들어갈 텍스트 블록을 `cache_control: ephemeral`과 함께
조립한다. **Anthropic의 `system` 파라미터는 텍스트만 허용하고 이미지를
받지 않는다** — 그래서 예시 프레임 이미지 자체는 태그처럼 유저 턴에
`[KEEP]`/`[DROP]` 라벨과 함께 첨부되고, 캐싱되는 시스템 블록에는 규칙과
segment_id/사유 텍스트만 들어간다. `taste_profile.json`/`examples.jsonl`
둘 다 없으면 이 블록 자체를 넣지 않는다(빈 토큰 낭비 방지).

`run.py review <work/project>`는 완료된 프로젝트의 EDL(`edl_baseline.json`
또는 `edl_ai.json`)과 `candidates.json`을 비교해 "선택된 컷"과 "탈락한 후보"를
하나씩 보여주고 keep/drop/skip + 사유를 입력받는다. 기록될 때마다 프레임을
`taste/examples/<project>__<segment_id>.jpg`로 복사하고 `taste/examples.jsonl`에
한 줄 append한다 — 이게 유일하게 사람의 판단이 파이프라인 데이터로 들어오는
지점이다.

`run.py`는 이 중 `split -> filter -> select(baseline) -> render -> grade`만
자동으로 돌린다(서브커맨드 없이 `python run.py footage/trip_01 ...`로 실행 —
`run.py`의 `DefaultGroup`이 `review`가 아닌 첫 인자를 전부 `pipeline`
서브커맨드로 보낸다). `sheet.py`/`beats.py`/`tag.py`/`select_ai.py`는 아직
오케스트레이터에 안 물려 있다 (`sheet.py`/`beats.py`가 스켈레톤이라 태깅·AI
선택 경로 전체가 아직 end-to-end로 안 돌아가기 때문 — 넷 다 갖춰지면 `--use-ai`
같은 옵션으로 연결).

## EDL 스키마 요약 (`pipeline/edl.py`)

- `Scene` — `split.py`가 쓰는 원시 세그먼트 (segment_id, source_file, start_sec, end_sec, duration, frame_path)
- `SegmentsFile` — `segments.json` 파일 전체 (project, segments: list[Scene])
- `Quality` — `filter.py`가 매기는 프레임 품질 판정 (blur_score, brightness, phash, verdict, duplicate_of)
- `Tags` — `tag.py`가 매기는 구조화 태그 (shot, subject, face_visible, mood, keep_score); 태깅 전에는 `None`
- `CandidateScene(Scene)` — `Scene` + `quality: Quality` + `tags: Tags | None`
- `CandidatesFile` — `candidates.json` 파일 전체 (project, candidates: list[CandidateScene], rejected: list[CandidateScene])
- `TimelineClip` — 최종 컷 하나 (order, segment_id, source_file, in_sec/out_sec, transition).
  `transition`은 `"cut"`(기본) 또는 `"flash"`(렌더러가 컷 진입에 3~5프레임 화이트 플래시를
  입힘 — 인트로→메인→아웃트로 경계에 `select_baseline.assign_transitions()`가 결정론적으로
  할당, 양 선택기 공유)
- `Caption` — 클립에 붙는 자막 하나 (segment_id + 클립 재생 구간 기준 상대 오프셋).
  절대 타임스탬프 없음 — LLM/선택기는 텍스트와 어느 segment_id에 붙일지만 정하고,
  오프셋 계산은 `pipeline/captions.py`가 한다 (`build_captions` 휴리스틱 /
  `build_captions_from_ai`는 select_ai의 submit_selection이 쓴 `caption` 텍스트 배치)
- `EDL` — `project`, `version`, `generator`, `canvas`, `frame`, `aesthetic`, `audio`, `timeline: list[TimelineClip]`, `captions: list[Caption]`, `signature`

`TimelineClip.segment_id`는 반드시 `candidates.json`의 `candidates` 배열에 있는
`segment_id`를 참조해야 하고, `in_sec`/`out_sec`는 그 세그먼트의 `[start_sec, end_sec]`
범위 안이어야 한다 — 이 불변식이 원칙 2번(타임코드는 FFmpeg/PySceneDetect 산출물만)을
코드 레벨에서 강제하는 지점이다. `validate_edl()`은 여기에 더해 컷 전환 지점의 비트
스냅 오차(기본 40ms 이내)와 총 길이가 목표 길이 ±1초 이내인지도 검사한다.

## render/ 아키텍처 노트 (다시 실수하지 않기 위한 기록)

`render/src/Root.tsx`(와 그게 import하는 모든 것)는 Remotion이 **브라우저용으로
번들링**한다 — `calculateMetadata`까지 포함해서, 헤드리스 Chrome 안에서 평가된다.
그래서 다음 두 가지가 성립한다:

1. `Root.tsx`/`ZlogFilm.tsx`/`Clip.tsx` 안에서는 `node:fs`/`node:path`를 import할
   수 없다 (웹팩이 "UnhandledSchemeError"로 즉시 빌드 실패시킨다).
2. `OffthreadVideo`/`Audio`의 `src`는 로컬 절대경로나 `file://` URI를 받아주지
   않는다 — `staticFile()`로 `render/public/` 밑의 상대경로만 받는다.

그래서 EDL의 `source_file`/`bgm_id`를 실제 파일로 바꾸는 작업은 **번들에 들어가지
않는 플레인 Node 스크립트인 `render/resolve-props.mjs`**가 미리 해둔다: 필요한
비디오/BGM 파일을 `render/public/`에 심볼릭 링크(안 되면 복사)해두고, 그 결과인
"resolved props" JSON을 `--props`로 넘긴다. `Root.tsx`의 `calculateMetadata`는
그 결과(이미 절대/상대 경로가 다 채워진 EDL)를 받아서 순수 산술로 `durationInFrames`만
계산한다 — 파일시스템을 볼 필요가 없다.

**결론: `edl_baseline.json`/`edl_ai.json`을 `remotion render`에 직접 `--props`로
넘기면 안 된다.** 항상 먼저 `resolve-props.mjs`를 거쳐야 한다 (아래 실행 방법 참고).
`render/public/`은 매 렌더마다 다시 채워지는 스테이징 폴더라 gitignore되어 있다.

브이로그 연출은 전부 렌더 단계의 결정론적 효과다 (원칙 4) — Ken Burns 줌/팬과
컷 펀치인(`Clip.tsx`, order 기반), CCD 캠코더 OSD(`CamcorderOverlay.tsx` — REC
점멸/타임코드/날짜 스탬프, 날짜는 `resolve-props.mjs`가 `shot_date`로 주입),
그레인·스캔라인·비네트(`FilmLook.tsx`), 플래시 전환(`ZlogFilm.tsx`), 박스 자막
팝인(`Caption.tsx`, 4:3 프레임 기준 배치). LLM은 이 중 어느 것도 제어하지 않는다.

비트 그리드가 BGM 길이에서 끝나므로, BGM보다 긴 원본의 후행 세그먼트가 스냅
불가로 검증에 실패하지 않도록 두 선택기 모두 로드 시
`select_baseline.extend_beat_grid()`로 그리드를 같은 템포로 후보 최장 지점까지
산술 연장해서 쓴다 (리듬은 여전히 librosa 템포가 결정).

## 실행 방법

```bash
uv sync
cd render && npm install && cd ..
cp .env.example .env   # ANTHROPIC_API_KEY 채우기 (select_ai.py 쓸 때만 필요)
```

전체 파이프라인(휴리스틱 선택 기준)은 오케스트레이터 하나로 돌아간다:

```bash
python run.py footage/trip_01 --bgm y2k_synth_01 --duration 15
```

`--bgm`은 `assets/bgm/<name>.mp3`(또는 `.wav`)와 그 옆의 `<name>.beats.json`을
가리키는 이름이다 — beats.json은 아직 `beats.py`가 스켈레톤이라 사람이 미리
만들어 둬야 한다. 이미 있는 산출물은 스킵하고(`--force`로 무시), `--from
<stage>`로 특정 단계부터 다시 시작할 수 있다 (`split`/`filter`/`select`/
`render`/`grade` 중 하나). 실패하면 어느 단계에서 몇 초 만에 왜 실패했는지
stderr에 찍고 종료한다.

완료된 프로젝트를 놓고 직접 keep/drop을 판정해 취향 데이터를 쌓으려면:

```bash
python run.py review work/trip_01
```

각 단계는 독립 실행도 가능하다 (원칙 5). `project`는 `footage/<project>/`
디렉토리 이름에서 가져온다:

```bash
python -m pipeline.split footage/trip_01
python -m pipeline.filter --project trip_01
python -m pipeline.select_baseline --project trip_01 --bgm-track assets/bgm/track1.mp3 --target-duration-s 15
python -m pipeline.tag --project trip_01
python -m pipeline.select_ai --project trip_01 --bgm-track assets/bgm/track1.mp3 --target-duration-s 15
python -m pipeline.grade --input work/trip_01/render.mp4 --lut assets/luts/ccd_cool_01.cube --output work/trip_01/final.mp4
```

렌더는 두 단계다 — `resolve-props.mjs`가 먼저 EDL의 파일 참조를 실제 경로로
바꿔야 한다 (render/ 아키텍처 노트 참고):

```bash
cd render
node resolve-props.mjs ../work/trip_01/edl_baseline.json ../work/trip_01/edl_resolved.json
npx remotion render src/index.ts ZlogFilm ../work/trip_01/render.mp4 --props=../work/trip_01/edl_resolved.json
```

`split.py`, `filter.py`, `select_baseline.py`, `tag.py`, `taste.py`,
`select_ai.py`, `grade.py`, `render/`, `run.py`(pipeline/review 둘 다)는
구현되어 있다. `sheet.py`, `beats.py`, `evals/`는 아직 `NotImplementedError`를
던지는 스켈레톤이다 — 이 둘이 없으면 `tag.py`/`select_ai.py`도 실제로는
(컨택트 시트와 비트 그리드가 사람이 미리 만들어둔 게 아닌 이상) 끝까지 돌지
않는다.

## 지금 하지 말 것

- 웹앱/API 서버를 만들지 않는다. 지금은 로컬 CLI뿐이다.
- `pipeline/sheet.py`, `pipeline/beats.py`, `evals/`는 스켈레톤만 존재 — 지시
  없이 실제 로직을 채우지 않는다.
- 위 아키텍처 대원칙 5가지를 우회하는 "임시 방편"을 추가하지 않는다 (예: LLM이 프레임
  타임스탬프를 텍스트로 추정해서 쓰는 fallback 등).
- API 키를 코드/문서/커밋/채팅에 절대 하드코딩하지 않는다. `.env`(gitignore됨)에서만
  읽는다 — 채팅에 키가 노출되면 즉시 폐기(rotate)하라고 알린다.
