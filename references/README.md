# Zlog reference-learning workspace

이 폴더는 완성된 레퍼런스 영상을 구조화된 편집 학습 데이터로 바꾸는 로컬 환경입니다.
영상 원본과 생성 산출물은 Git에 올라가지 않습니다.

1. `python -m pipeline.reference_learning init --root references`
2. 완성된 레퍼런스 영상을 `references/inbox/`에 넣습니다.
3. 선택적으로 `references/annotations/<영상 파일명>.json`을 만듭니다.
4. `python -m pipeline.reference_learning analyze --root references`
5. 결과는 `references/library/<이름>-<해시>/`와 `references/library/catalog.json`에 생성됩니다.

세부 입력 규격과 자동/수동 분석 범위는 `docs/reference-learning.md`를 참고하세요.
