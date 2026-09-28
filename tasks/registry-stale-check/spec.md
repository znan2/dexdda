목표: data/registry.json이 오래됐을 때 대시보드에서 경고를 볼 수 있다.
비목표: 레지스트리 자동 재생성, 스케줄러 추가, UI 디자인 변경.
수용 기준:
  - registry.json 최상위에 generated_at(ISO 8601 UTC)이 기록된다. build_registry.py가 생성 시 기록한다.
  - 필드가 없는 기존 registry.json도 로드되며, 이 경우 파일 mtime을 폴백으로 쓴다.
  - /api/health 응답에 registry_generated_at(ISO 8601), registry_age_hours(float), registry_time_source("field" | "mtime")가 포함된다.
  - 나이가 config/settings.toml의 registry_max_age_hours(기본 72)를 넘으면 /api/health에 registry_stale: true가 포함된다.
  - 프론트 헤더에 stale이면 "레지스트리 오래됨 (N시간)" 배지가 표시된다.
현재 증거: registry.json에 생성 시각 없음(최상위 키: schema_version, source_digest, source_markets, chains, coins, issues). Registry 모델 extra="forbid".
수정 범위: app/registry/models.py(generated_at 선택 필드), app/registry/builder.py(기록), app/registry/loader.py(읽기·폴백), app/config.py(Settings), app/main.py의 /api/health, src/app/web 헤더. 그 외 파일은 건드리지 않는다.
검증: uv run pytest -q, uv run ruff check src tests scripts. Settings 필드 추가 시 settings.toml 예시 갱신. registry 재생성은 실API 없이 --sources/--metadata 스냅샷 모드로만.
제약: 실API 호출 없음. 기존 registry.json 호환 유지.
반환: 변경 파일 목록, 실행한 검사 결과, 미해결 사항.
결과: 2026-09-22 구현 완료. 브라우저 배지 표시는 Playwright 미설치로 미확인.
