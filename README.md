# zlog

사진·영상 원본을 넣으면 짧은 세로 브이로그 mp4가 나오는 **하이브리드 Claude + GPT** 편집 파이프라인.

- 기본 스타일: `clean_vlog` (1080×1920, 자연 색감)
- 옵션: `y2k_camcorder` (명시적 Y2K/CCD 요청 시)
- 로컬 CLI + FastAPI(`server.py`) + Vite `web/` + Next.js Studio(`next-web/`)

아키텍처 원칙·단계별 I/O 계약은 [CLAUDE.md](CLAUDE.md) 참고.
현재 프로덕션 경로 요약은 [evals/production_path.md](evals/production_path.md).

## 최종 파이프라인

```
업로드(+upload_order)
→ 장면 분할 (split)
→ 적응형 증거 프레임 + 결정론적 특징 (evidence)
→ 품질 필터 / 컨택트 시트
→ Claude AssetAnalysis (Haiku, 모드별 Sonnet 승급)
→ Claude StoryPlan (Sonnet)
→ 코드 TimelinePlan
→ 코드 품질 검사 + GPT-5.6 Luna 평가 (economy는 코드만)
→ 필요 시 Claude 1회 수정 → (premium) Sol 1회
→ Remotion 렌더 → FFmpeg 오디오 믹스 → final.mp4
```

### 품질 모드 (`--quality-mode` / `quality_mode` form)

| 모드 | 분석 | 감독 | 평가 | Sol |
|---|---|---|---|---|
| economy | Haiku only | Sonnet | 코드만 (GPT 없음) | off |
| balanced | Haiku + 선택 Sonnet | Sonnet | Luna + Claude 1회 | off |
| premium | Haiku + 적극 Sonnet | Sonnet | Luna + Claude + Sol 1회 | on if needed |

## 설치

```bash
uv sync
cd render && npm install && cd ..
cd web && npm install && cd ..
cd next-web && npm install && cd ..
cp .env.example .env   # ANTHROPIC_API_KEY, optional OPENAI_API_KEY
```

ffmpeg가 PATH에 있어야 한다.

## CLI

```bash
python run.py footage/trip_01 --bgm demo_track --duration 15
python run.py footage/trip_01 --bgm demo_track --quality-mode premium --force
python run.py footage/trip_01 --bgm demo_track --baseline   # legacy cut
python run.py review work/trip_01
```

## 웹 / Studio

```bash
# API
uv run uvicorn server:app --host 127.0.0.1 --port 8000

# Vite composer (local)
cd web && npm run dev

# Next.js Studio (billing + Pro-gated composer → ZLOG_API_BASE)
cd next-web && npm run dev
```

`POST /api/jobs` accepts `note`, `files`, `quality_mode`, `dev_mode`.
Dev mode exposes stage / provider / model / latency / tokens / cost / escalation — never API keys or system prompts.

YouTube 썸네일 → `taste_profile.json` 자동 덮어쓰기는 **비활성**  
(`ZLOG_ALLOW_YOUTUBE_TASTE_OVERWRITE=1` 일 때만 옵트인).

## 평가

```bash
uv run pytest evals -q
```

시나리오 카탈로그: `evals/scenarios/catalog.json` (10개 입력 유형).
모드 비교 헬퍼: `evals/compare_modes.py`.

대략 숏당 모델 비용 추정(카탈로그): baseline $0 · economy ~$0.04 · balanced ~$0.09 · premium ~$0.18.

## 디렉토리

```
zlog/
  run.py                 # CLI 오케스트레이터
  server.py              # FastAPI job API
  pipeline/              # split…evaluate_plan, product_pipeline, ai/
  render/                # Remotion
  web/                   # Vite composer
  next-web/              # Next.js Studio + billing
  work/                  # 중간 산출물
  footage/               # 원본
  assets/                # BGM, LUT
  evals/                 # pytest + scenarios
  taste/                 # founder taste (수동 / review)
```
