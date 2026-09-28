# 신규 종목 감지와 매핑 보류 분리

2026-09-20 수정 완료. 서버 재시작 및 브라우저 새로고침 후 적용된다.

## 원인과 수정

기존 감지기는 거래소 전체 마켓에서 최종 레지스트리 등록 마켓을 빼고 모두 신규 상장으로 판단했다. 수집 시 이미 있던 종목도 CoinGecko 연결 누락으로 레지스트리에 등록되지 못하면 매번 신규 종목으로 표시됐다.

- 레지스트리 빌드 결과에 `source_markets`를 추가해 원본 거래소 KRW 목록을 보존한다. 매핑 실패 종목도 포함한다.
- `MarketWatch`는 원본 목록과 관측 이력을 기준으로 처음 발견한 종목을 구분한다. 이는 공식 상장 공지 시각을 판정하는 기능이 아니다.
- `data/listing_state.json`에 관측 이력과 미등록 신규 종목을 저장한다. 재시작 후에도 분류를 유지한다. API나 저장 실패 시 새 항목을 확인 처리하지 않는다.
- 원본 목록과 저장 이력이 없는 구버전에서는 첫 정상 조회를 기준 목록으로 삼는다. 최초 목록 전체를 신규로 알리지 않는다.
- 이전부터 있던 미등록 종목은 API `mapping_pending`으로 반환하고, 화면 하단의 접힌 ‘등록 확인’에서만 사유를 보여준다. 해당 항목이 없으면 이 구역은 숨긴다.
- 실제 새로 발견된 종목은 ‘새 종목 감지 · 등록 확인 필요’로 표시한다. 검증 없이 거래 경로를 자동 추가하거나 재빌드만 하면 된다고 안내하지 않는다.
- 재빌드 후 등록된 종목은 알림에서 빠진다. 새 목록에 포함됐지만 여전히 매핑 보류인 항목은 등록 확인으로 이동한다.

## 확인한 연결 정보

`config/overrides.toml`에 거래소별 매핑 세 건을 명시했다. 체인·주소·직접 입금 가능 여부를 강제로 변경하는 설정은 추가하지 않았다.

- 업비트 TAO → `bittensor`: [업비트 프로젝트/네트워크](https://datalab.upbit.com/assets/TAO/summary), [CoinGecko API ID 및 프로젝트](https://www.coingecko.com/en/coins/bittensor). 기존 Base/Robinhood 매수 후보는 브릿지 후보로 유지.
- 빗썸 ARK → `ark`: [빗썸 Ark Mainnet 및 ark.io 확인](https://feed.bithumb.com/notice/1644435), [CoinGecko ARK](https://www.coingecko.com/en/coins/ark). 지원 EVM 컨트랙트가 없어 후보에서 계속 제외.
- 빗썸 PROS → `pharos-network`: [빗썸 Pharos Network 설명서](https://feed-content.bithumb.com/cms/3542c89a-6579-46a7-ade3-982f4e2622f8.pdf), [CoinGecko Pharos의 API ID](https://www.coingecko.com/en/coins/pharos). 저장된 거래소 wallet PROS/Pharos, 플랫폼 pharos의 chain ID 1672와 native_coin_id도 대조. 오래된 ticker ID `pharos-2`를 사용하지 않음.

저장된 공개 sources/metadata로 재빌드했으며, 세 코인 외 레지스트리 코인 데이터 변경이 없는지 확인했다. 토큰 체인·주소·decimals·native 값은 동일하다. 이전 registry.json과 보고서는 data/registry-backups/listing-fix-*에 백업했다.

## 검증

- Python 270개 테스트 통과.
- 신규/매핑 보류 분리, 반복 관측, 재시작, 재빌드, 구버전·손상 캐시, 저장 실패, 원본 마켓 보존을 검증했다.
- Chrome 합성 검증에서 기존 보류 종목은 상단 알림에 나타나지 않고 접힌 등록 확인 목록에 표시된다. 기존 거래 확인 흐름도 통과.
- 적용한 레지스트리로 양 거래소의 실제 공개 시장 목록을 조회한 결과: 업비트/빗썸 모두 신규 0건, 매핑 보류 0건, 조회 오류 없음. 이 검증은 거래 전송 없이 종목 목록만 조회했다.
