# M5~M7 검증 기록

2026-09-20. 필수 구현과 DRY_RUN 검증을 완료했다. 실제 자금 서명·전송 및 소액 실거래 검증은 수행하지 않았다.

## 완료 기준과 근거

| 요구사항 | 구현·검증 |
|---|---|
| 확인 후 실행 | 서버가 만든 intent만 확인 가능. 모달을 열기만 해서는 실행 안 됨. 세션 토큰·Origin 검사 |
| DRY_RUN | reset·approve·swap 계획 끝까지 처리, signer 및 broadcast 호출 0회. 하위 서명/RPC 계층에도 별도 차단 |
| 실행 가드 | 최대 금액, USDT/네이티브 잔고, CA, bridge, 체인 미준비, 위험 경고, 최소갭, 승인 상한, nonce, allowance 변경 검사 |
| 승인 | 정확한 approve selector·spender·유한 금액 검증. 기존 비영(非零) allowance 부족 시 0 reset. exact/capped 합성 테스트 |
| 스왑 응답 | from/chain/router/value/data/gas/minReceiveAmount/slippage 검증. 잘못된 API 응답을 서명 전에 거부 |
| 가스 계산 | swap 비용을 중복 합산하지 않고 큰 추정치 사용. 승인 후에도 해당 비용을 전체 수익에서 제외하지 않음 |
| 전송 복구 | broadcast 전 hash 영속화, timeout·응답 불확실·revert·nonce 충돌, 재확인/재시작 시 중복 전송 방지 |
| 실제 수령량 | 성공 영수증 후 history의 체인·해시·지갑·토큰 주소 검증. 정수 base unit과 .000 형식, 큰 숫자 정밀도 검사 |
| 수령량 API 실패 | 이미 성공한 스왑을 success/history_pending으로 유지. 조회 재시도로 재전송 안 됨 |
| 전체 입금 주소 | 472개 ready, pending/error 0. 전체 동기화 재실행에서 생성 호출 0회 및 캐시 바이트 동일 |
| 주소 경계 | 정확한 체인·net_type·coin_id·CA·계정 매칭. 다른 네트워크·계정 주소 미표시 |
| 완료 화면 | 합성 성공 이력의 실제 수량, 양 거래소 주소·메모 복사, 확대 대조, 모바일 넘침 검사 |
| 운영 정보 | 신규 마켓 감지, 환율 실패 격리, ECB 날짜·교차환율, 비밀값 허용목록 로그 검사 |

## 자동 검증

- Python 테스트 **221 passed**.
- Ruff lint/format 통과.
- 기존 M3 목록·M4 상세 Chrome 회귀 검사 통과.
- M5~M7 Chrome 합성 검사 통과: 확인 모달 → DRY_RUN 완료 → 이력 조회 → 완료 화면 → 주소·메모 클립보드 복사.
- 브라우저 pageerror 0. 데스크톱 및 390px 모바일 확인.
- 테스트 API·RPC는 합성 응답을 사용한다. 서명 단위 테스트의 키는 공개된 테스트 전용 상수이며 실제 .env/계정 키를 읽지 않는다. 가짜 네트워크 전송만 사용한다.

재현 명령:

```sh
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
node scripts/check_ui.cjs
node scripts/check_detail_ui.cjs
node scripts/check_execution_ui.cjs
```

브라우저 검증에는 Playwright와 설치된 Google Chrome이 필요하다. 이 환경에서는 앱의 번들 Node/Playwright를 사용했으며 브라우저·임시 서버는 검증 후 종료했다. 명시적인 --live 옵션이 없는 브라우저 검증은 실제 API를 사용하지 않는다.

화면을 직접 확인했다:

- [확인 모달](screenshots/m5-confirm-desktop.png)
- [DRY_RUN 완료](screenshots/m5-dry-run.png)
- [완료 화면](screenshots/m6-completion-desktop.png)
- [완료 화면 모바일](screenshots/m6-completion-mobile.png)

## 실제 서비스 검증

- 전체 주소 준비 전: 대상 472개 중 기존 주소 173개, 미준비 299개.
- 전체 동기화 후: **472개 ready**, error/pending 0.
- 두 번째 전체 동기화: create 호출을 금지한 검증에서도 **0회 생성**, 캐시 바이트 동일. 주소는 외부 출력 없이 로컬 비공개 캐시에만 저장했다.
- Ethereum에서 실제 OKX unsigned approve/swap 응답을 조회했다. 1 USDT→ETH 요청의 체인·토큰·금액·spender·router·최소 수령량 검증이 통과했다. 개인키 서명·RPC 전송은 0회다.
- 업비트·빗썸 실제 공개 마켓 조회 오류 0. 당시 레지스트리에 미등록 마켓 각각 1개·2개를 감지했다. 재빌드 필요 알림 대상이며 자동 등록하지 않았다.
- ECB 조회 성공. 관측 기준일 2026-09-18을 지연된 참고값으로 처리한다.
- 실제 설정의 DRY_RUN=true 유지. .env를 수정하지 않았다.
- 통합 서버 lifespan 시작 성공, startup_error 없음, 가격 API 200 및 swap/address/operations 서비스 연결 확인. 실제 스왑 준비·확인 API는 호출하지 않았다.
- 최종 로컬 확인에서 주소 472개 ready, .env와 주소 캐시 파일 권한 600, 검증 전후 .env 바이트 동일.
- 기존 레지스트리와 검토 리포트의 SHA256 유지:
  - registry: cfed1121bb91ba7dff0dcdb0cb0620a219a1d56774f312d4111eb600140bc656
  - report: 1fd186742e6ba932ca7035c9e29e73068479569f583803f2d8bdcc6ec80d404c

## 검증 범위

실제 체인에서의 성공·revert·nonce 경합은 합성 RPC로 검증했다. 실거래 성공을 주장하지 않는다. 소액 실거래는 계획서 M5의 사용자 직접 검증 항목이다.

현재 실행 준비 체인은 비용 검증이 끝난 Ethereum·Polygon·Avalanche C이며 각 체인의 실제 잔고·RPC·입금 상태 가드를 추가로 충족해야 한다. 나머지 조회 체인의 추가 가스 모델은 보류한다. 수신 주소로 전송하는 기능은 포함하지 않으며, 전송 gas는 설정 예산이다.

유료 API 선택 및 Solana/MEV/다른 DEX는 보류·선택 항목이다. 세부 운용은 [사용 안내](m5-m7-usage.md)를 참고한다.
