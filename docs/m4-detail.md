# M4 상세 견적 사용 안내

목록의 행이나 종목 버튼을 누르면 상세 패널이 열린다. 기본 금액은 settings.toml의 pricing.default_amount_usdt(1,000 USDT)다. 패널을 열거나 금액·매수 자산·매도 거래소를 바꿔도 자동 조회하지 않는다. 설정을 마친 뒤 ‘견적 갱신’을 눌러야 새 견적을 받는다. 설정 변경 시 기존 견적과 실행 가능 상태를 무효화하며, 진행 중이던 이전 요청의 응답은 버리고 자동 재조회하지 않는다. Enter로 종목을 열고 Esc로 닫을 수 있다.

상세 견적 조회에는 개인키가 필요 없다. 실행은 [M5~M7 안내](m5-m7-usage.md)의 확인 및 가드를 별도로 통과해야 한다. 입금 중단의 참고 실효갭은 실행 조건으로 사용하지 않는다.

## 계산 기준

1. 레지스트리의 coin_id + chain_index + CA + 거래소 경로를 검증한다.
2. 견적 갱신을 요청하면 직접 입금 경로의 거래소 입출금 상태를 다시 조회하고 현재 체인과 일치하는 net_type을 선택한다.
3. 검증된 USDT/USDT0 CA와 decimals로 금액을 최소 단위 정수로 바꿔 OKX quote를 요청한다.
4. toTokenAmount를 수량으로 환산한다. taxRate가 있으면 매수세를 보수적으로 적용하고 최소 단위 아래는 버린다.
5. 거래소 매수 호가를 높은 가격부터 소진한다. 일부 호가만 체결되면 전량 매도 손익을 계산하지 않는다.
6. 업비트 KRW-USDT 매도 1호가를 두 거래소 모두의 환산 기준으로 사용한다.

`실효갭 = (매도대금 KRW / 업비트 USDT 매도 1호가 - 가스비 USDT - 투입 USDT) / 투입 USDT`

예상 손익 KRW는 예상 손익 USDT × 같은 환산 호가다. 업비트·빗썸 거래 수수료는 제외한다. USD 가스비의 USDT 환산에는 1 USD≈1 USDT를 가정한다.

## 가스비 추정

- swap: OKX quote.tradeFee. 이 필드는 거래소 매매 수수료가 아닌 USD 네트워크 비용이다. estimateGasFee를 USD로 오인하지 않는다.
- approve: OKX가 반환한 gasLimit × RPC eth_gasPrice × 네이티브 가격. allowance 충분 시 0회, 0이면 1회, 부족한 비영(非零) allowance는 USDT reset을 고려해 2회를 예산에 반영한다.
- transfer: 전송 1회 예산 × RPC gasPrice × 네이티브 가격. 기본 ERC20 100,000 gas, native 21,000 gas이며 settings.toml의 detail 항목에서 조정한다.
- 이 전송 예산은 실제 수신 주소에 대한 시뮬레이션이 아니다. 토큰 로직·스마트 컨트랙트 수신 주소 등에 따라 달라진다. M5/M6 실행 전 실제 트랜잭션으로 재추정해야 한다.
- 추가 체인 비용을 검증하지 못했거나 RPC·지갑 주소·가격·allowance가 없으면 비용을 0으로 대체하지 않는다. 부분 비용과 사유만 표시하고 실효갭을 보류한다.

## 지원 범위와 확인 필요 항목

- 목록의 29개 EVM 체인은 유지한다. 매수 자산 주소는 공식 Tether/USDT0 문서에서 확인한 **12개 체인**을 quote_assets.toml에 등록했다. USDT0는 USDT와 구분해 표시한다. USDC를 임의로 대신 사용하지 않는다.
- 현재 Ethereum·Polygon·Avalanche C는 추가 데이터 수수료 없는 일반 EVM 가스 계산을 활성화했다. 그 외 등록 체인은 견적·호가 조회와 부분 가스 표시를 지원하며, 체인별 추가 비용 검증 후 전체 비용 계산을 확장한다.
- 매수 자산이 아직 검증되지 않은 체인도 상세 입금 상태와 설정 부족 사유를 보여준다. 해당 경로를 목록에서 제거하지 않는다.
- 업비트는 deposits/chance/coin의 최소 입금량·컨펌 수를 조회한다. 최근 20블록 평균 시간 × 컨펌 수를 참고 시간으로 제공한다. 거래소 처리·점검 지연은 포함하지 않는다.
- 빗썸은 현재 연결한 API에서 컨펌 수·최소 입금량을 얻지 못해 값을 만들지 않는다. 입출금 현황을 별도 조회하고 미제공 항목을 표시한다.
- 입금 중단/미확인 또는 최소 입금량 미달만 문제라면 별도의 입금 가능 가정 참고 실효갭을 표시하고 실행은 차단한다. 허니팟/미확인, 세율 미확인 또는 양수, 호가 부족, 비용 누락, 만료 시 참고값도 보류한다. 세금 토큰은 전송세까지 확정되지 않아 세금 조정 수량만 참고로 표시한다.
- 브릿지는 조회 전용이며 브릿지 비용 없는 실효갭을 제공하지 않는다.
- UI 갱신 주기와 별도로 상세 견적은 기본 15초 유효하다. 만료된 손익을 숨기며, 새 금액 입력 즉시 이전 결과를 지운다.
- quote 조회는 한 번에 하나만 진행한다. OKX·CEX 수집기와 호출 게이트를 공유한다. 지연/호출 제한/요금제 문제는 사유를 표시하며 유료 전환하지 않는다.

## API와 검증

`POST /api/detail` JSON: coin_id, chain_index, address, exchange(upbit/bithumb), amount_usdt(문자열).

읽기 전용 요청이다. 입력은 최대 1,000,000 USDT, 소수점 6자리이며 이 상한은 거래 한도가 아니다. 등록되지 않은 경로는 404, 형식 오류 422, 진행 중 중복 요청 429, 45초 제한 초과 504다. 응답에 비밀값·지갑 주소·서명·calldata를 포함하지 않는다.

- 단위/API: `uv run pytest -q`
- 화면: `node scripts/check_detail_ui.cjs` (Playwright 및 Chrome 필요)
- 실제 조회: `node scripts/check_detail_ui.cjs --live` (로컬 .env 사용, 거래 없음)

## 근거 문서

- [OKX quote](https://web3.okx.com/onchainos/dev-docs/trade/dex-get-quote), [unsigned approval](https://web3.okx.com/onchainos/dev-docs/trade/dex-approve-transaction)
- [업비트 입금 가능 정보](https://docs.upbit.com/kr/reference/available-deposit-information), [호가](https://docs.upbit.com/kr/reference/list-orderbooks)
- [빗썸 호가](https://apidocs.bithumb.com/reference/호가-조회)
- [Tether 지원 프로토콜](https://tether.to/en/supported-protocols/), [USDT0 배포](https://docs.usdt0.to/technical-documentation/deployments), [USDT0 개발자 문서](https://docs.usdt0.to/technical-documentation/developer)

코인별 비교·만료 사유 표시의 최신 동작은 [피드백 반영 기록](dashboard-feedback.md)을 참고한다.
