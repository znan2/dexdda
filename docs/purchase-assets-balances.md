# 매수 자산 선택과 체인별 잔고 (2026-09-20)

## 결정 및 동작

- 사용자가 USDT/USDC를 직접 선택한다. Robinhood는 USDG를 선택한다. 지원하는 자산만 선택 가능하다.
- 사용자 지시에 따라 1 USDC = 1 USDG = 1 USDT로 계산한다. 기존 업비트 USDT/KRW 매도 1호가 환산과 거래소 매매 수수료 제외 정책은 그대로다. 별도의 스테이블코인 환율 API를 호출하지 않는다.
- 선택한 자산의 실제 CA와 decimals로 OKX 견적·잔고·승인·스왑 요청을 구성한다. 다른 자산 잔고로 자동 대체하지 않는다. 라우터가 USDC → USDT → 대상 토큰 등의 중간 경로를 선택할 수 있지만, 별도 사전 USDT 전환 거래를 앱이 생성하지 않는다.
- 요청의 purchase_symbol 기본값은 USDT로 유지해 이전 요청 형식과 호환한다. amount_usdt 필드는 호환성을 위해 이름만 유지하며 선택한 자산의 투입 수량이다.
- 상세 패널 열기·금액/자산/거래소 변경 시에는 대기하고, 견적 갱신 버튼을 눌러야 조회한다. 자산을 바꾸면 이전 견적/실행 가능 상태를 즉시 무효화한다. 늦게 도착한 이전 자산 견적은 버린다. 실행 확인 창에 투입 자산·CA·승인 자산을 표시하고, 서버가 준비한 자산 설정을 고정한다. 확인 전에 자산 설정이 바뀌면 실행을 거절한다.
- 기존 체인 가스/라우터/입금/최소갭/개인키/DRY_RUN 가드는 유지한다. 특히 브릿지 후보는 가격 영향 조회만 지원하며 실행할 수 없다. 자산 추가가 미검증 체인의 실행을 활성화하지 않는다.

## 등록 자산

| 체인 | 자산 | decimals | CA |
| --- | --- | --- | --- |
| Ethereum 1 | USDT | 6 | 0xdac17f958d2ee523a2206206994597c13d831ec7 |
| Ethereum 1 | USDC | 6 | 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48 |
| Robinhood 4663 | USDG | 6 | 0x5fc5360d0400a0fd4f2af552add042d716f1d168 |
| Base 8453 | USDT (bridged) | 6 | 0xfde4c96c8593536e31f229ea8f37b2ada2699bb2 |
| Base 8453 | USDC | 6 | 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913 |
| Arbitrum 42161 | USDT0 | 6 | 0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9 |
| Arbitrum 42161 | native USDC | 6 | 0xaf88d065e77c8cc2239327c5edb3a432268e5831 |
| BNB Chain 56 | Binance-Peg USDT | 18 | 0x55d398326f99059ff775485246999027b3197955 |
| BNB Chain 56 | Binance-Peg USDC | 18 | 0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d |

기존 다른 체인의 USDT/USDT0 설정도 유지한다. 잔고는 표의 검증된 CA만 대상으로 한다. Arbitrum USDC.e나 같은 티커의 다른 브릿지 토큰은 합산하지 않는다. 세부 자산 명칭과 CA는 잔고 자산명 툴팁에서 확인할 수 있다.

출처:

- [Circle USDC 주소](https://developers.circle.com/stablecoins/usdc-contract-addresses)
- [Robinhood 공식 USDG 계약](https://docs.robinhood.com/chain/contracts/)
- [USDT0 공식 배포 API](https://docs.usdt0.to/api/deployments?product=usdt0)
- [Trust Wallet BSC USDC 등록 정보](https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/smartchain/assets/0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d/info.json)
- [Trust Wallet Base bridged USDT 등록 정보](https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/assets/0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2/info.json)

설정된 RPC 및 Robinhood 공식 공개 RPC에서 표의 9개 자산 모두 체인 ID·decimals·심볼을 확인했다. Arbitrum USDT0의 온체인 심볼은 USD₮0이다. BSC 두 자산은 18 decimals다.

## 잔고 수집

- .env의 WALLET_ADDRESS를 사용한다. 개인키·잔액 가격 API·서명·전송이 필요하지 않다.
- 서버에서 60초마다 수집한다. GET /api/balances와 브라우저의 15초 폴링은 메모리 캐시만 읽는다.
- 수동 새로고침은 POST /api/balances/refresh로, 기존 동일 출처/세션 검사를 사용한다. 동시 요청은 병합하고 10초 공통 쿨다운을 둔다.
- 초기 성공 시 5개 체인 확인 + 9개 decimals + 9개 balanceOf = RPC 23회. 이후 일반 주기는 5개 체인 확인 + 9개 balanceOf = 분당 14회다. 메타데이터 실패 시에는 검증을 재시도한다. OKX/CoinGecko 호출량은 증가하지 않는다.
- RPC는 설정값을 우선 사용한다. Robinhood의 RPC_URL_4663가 없을 때만 공식 공개 https://rpc.mainnet.chain.robinhood.com 을 잔고 조회용으로 사용한다. .env는 변경하지 않았다. 실제 실행용 RPC 설정 가드는 별개로 유지한다.
- 체인별 수집 동시성 2, 호출별 8초 제한. 한 체인/토큰의 실패가 다른 체인 잔고를 없애지 않는다.
- 처음부터 조회에 실패하면 —로 표시한다. 정상적인 0 잔고만 0.00으로 표시한다. 이후 실패하면 마지막 잔고·마지막 관측 시간·실패 사유를 표시하며, 정상 수신 후 120초를 넘으면 지연으로 표시한다.
- 상대 시간은 방금 전/몇 분 전/몇 시간 전/며칠 전이다. 숫자는 소수점 두 자리로 표시하고 원값은 툴팁에 남긴다. 0보다 크고 0.01 미만인 잔고는 <0.01로 표시한다.
- 잔고를 파일로 저장하지 않고, API에는 지갑 주소·RPC URL·키를 포함하지 않는다.

## 검증

- pytest 전체 289개 통과, Ruff 검사/포맷 통과.
- check_assets_ui.cjs: 자산 선택, 1:1 실효갭, USDC 확인·승인 표시, 합성 DRY_RUN, 이전 자산 응답 경쟁, USDG 브릿지 견적, 다섯 잔고 카드, 실패 시 잔고 유지, 모바일 레이아웃 검증.
- check_feedback_ui.cjs / check_detail_ui.cjs / check_execution_ui.cjs 기존 회귀 검증 통과. 브라우저 콘솔 오류 0.
- 실제 지갑의 9개 잔고 모두 정상 수신 확인. 금액·지갑 주소·RPC URL은 로그로 출력하지 않았다.
- 100 USDC → CAP(BSC), 100 USDG → TAO(Robinhood) 읽기 전용 견적 각각 정상 수신. 실제 거래 서명/전송 없음.
- 적용은 서버 재시작 후 브라우저 새로고침으로 한다.
