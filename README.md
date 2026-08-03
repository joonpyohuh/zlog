# zlog

사진·영상 원본을 넣으면 15~60초짜리 브이로그 mp4가 나오는 AI 편집 파이프라인 프로토타입.
2000년대 CCD 캠코더 감성의 Y2K 레트로 룩(쿨톤, 4:3 레터박스), 마지막 2초 "directed by
zlog" 엔딩 크레딧.

지금은 로컬 CLI 스크립트 단계다. 웹앱은 아직 없다.

> 아키텍처 원칙과 파이프라인 단계별 입출력 계약은 [CLAUDE.md](CLAUDE.md) 참고.

## 상태

스켈레톤. 각 단계는 인터페이스만 정의되어 있고 실제 로직은 미구현.

## 설치

```bash
uv sync
```

ffmpeg가 시스템에 설치되어 있어야 한다.

## 사용법

```bash
# 원본을 footage/<project>/ 에 넣은 뒤 (예: footage/trip_01/)
python run.py full footage/trip_01 --bgm-track assets/bgm/<track>.mp3
```

단계별로 따로 실행하려면 [CLAUDE.md](CLAUDE.md)의 "실행 방법" 참고.

## 디렉토리

```
zlog/
  run.py            # 오케스트레이터
  pipeline/          # 파이프라인 각 단계
  render/            # Remotion 렌더 프로젝트 (나중)
  work/              # 중간 산출물 (gitignore)
  footage/           # 원본 (gitignore)
  assets/            # BGM, 비트 그리드, LUT
  evals/             # 골든 데이터셋 (나중)
```
