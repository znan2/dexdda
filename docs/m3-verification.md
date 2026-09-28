# M3 검증 기록

2026-09-20. M3 목록 UI와 화면용 API 정보를 구현했다.

## 완료 기준별 근거

| 요구사항 | 확인 근거 |
|---|---|
| 티커·OKX 토큰명·한글명·체인·DEX 가격·양 거래소 갭 | 실제 Chrome 화면 및 API 메타데이터 테스트 |
| 최고 갭 내림차순 | fixture에서 beta 20%, alpha 10%, zero 0%, negative -10%, stale 순서 확인 |
| 정렬 변경 | 업비트/빗썸/티커 옵션의 실제 DOM 순서 확인 |
| 브릿지·의심 목록 접기 | summary 클릭으로 펼침, 다음 자동 갱신 뒤 open 유지 확인 |
| USDT 매도 1호가·마지막 갱신·DRY_RUN | 상단 실제 표시 및 갱신 시각 변화, true/false 두 상태 검증 |
| 입금 중단 코인 유지·네트워크 미지원 구별 | withdraw_only 경로가 메인 목록에 남고 입금 중단 배지 표시. 브릿지에는 직접 입금 불가 표시 |
| 주기적 갱신 | 반복 /api/gaps 응답과 화면 갱신 시각 변화 확인 |
| 레지스트리 외 같은 티커 토큰 미표시 | 외부 가격 저장소에 다른 CA/Artificial Inu 시세를 넣어도 표시 행은 gensyn의 등록된 주소뿐임을 테스트 |
| 실제 표시 경로의 식별 일치 | 실시간 브라우저 DOM의 토큰 534개를 registry coin_id·chain_index·address 집합과 전수 대조 |
| 신선도·장애 복구 | API 연결 차단 후 만료된 모든 갭/USDT가 대시로 바뀜. 연결 복구 시 순위·가격 자동 복구 |
| 검색·빈 목록 | 한글명/전체 CA 검색, 검색 결과 없음, 전체 빈 목록 확인 |
| 모바일 | 390px 화면에서 페이지 가로 넘침 없음, 표 내부 스크롤 가능 |
| 외부 텍스트 안전 표시 | 토큰명에 img/onerror 문자열을 넣어도 HTML 요소·스크립트가 생성되지 않음 |
| 로컬 정적 파일 경계 | CSS/JS 정상 제공, 외부 Host 차단, .env/경로 순회 접근 404 |

## 실행 결과

- Python: **141 passed** (M0~M2 회귀 테스트 포함).
- Ruff lint/format 및 JavaScript 구문 검사 통과.
- `scripts/check_ui.cjs`: 오프라인 Chrome 검증 통과, pageerror 0.
- `scripts/check_ui.cjs --live`: 실제 API·Chrome 검증 통과, pageerror 0.
- 라이브 API 관측값: 메인 305행, 브릿지 247행, 의심 22행. 조회 시점마다 바뀌므로 스크린샷의 숫자와 다를 수 있다.
- 일반 브라우저 검증은 합성 fixture만 사용한다. 라이브 검증은 읽기 전용 가격 수집이다.
- 검증용 서버와 브라우저는 스크립트의 finally에서 종료한다.

스크린샷을 직접 확인해 표·배지·접이식 섹션의 배치와 모바일 스크롤 구성을 검토했다.

- [실제 시세 화면](screenshots/m3-live.png)
- [합성 fixture 데스크톱](screenshots/m3-fixture-desktop.png)
- [합성 fixture 모바일](screenshots/m3-fixture-mobile.png)

## 남은 범위

입출금 배지는 레지스트리 기준이다. M4의 실시간 상세 조회·실효갭 견적은 구현하지 않았다.
유료 API 선택도 보류 상태를 유지한다. 갭은 업비트 USDT 공통 기준이며 거래소 수수료를 제외한다.
