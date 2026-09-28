# M1 레지스트리

## 실행과 산출물

프로젝트 루트에서 실행한다.

```sh
uv run scripts/build_registry.py
```

- `data/registry.json`: 코인 ID, 거래소별 마켓·네트워크, 체인별 컨트랙트와 OKX 메타데이터.
- `data/registry_report.md`: 분류 집계, 체인 범위, 미매핑 네트워크, ID 누락/충돌, OKX 미인식 CA.

명령은 두 거래소의 전체 KRW 마켓과 입출금 상태, CoinGecko 거래소의 모든 티커 페이지, 플랫폼·CA,
OKX Market/Swap 지원 체인과 토큰 기본 정보를 읽는다. 별도 종목 선택은 필요 없다.

전체 입력 수집과 검증에 성공한 뒤 산출물을 교체한다. API 권한·과금·호출 제한·응답 오류가 해결되지 않으면 실패 코드로 종료하고 기존 레지스트리를 유지한다.
미매핑 네트워크나 일부 토큰 미인식은 리포트 경고로 남기고 나머지 검증된 항목을 생성한다.
이전 파일이 보존된 경우 최신 빌드 성공으로 오인하지 않도록 종료 코드를 확인한다.

## 분류 기준

`tradable`은 해당 거래소가 지원하는 체인에서 매핑된 토큰이라는 뜻이다.
현재 입금 가능 상태나 지갑의 실행 준비 완료를 의미하지 않는다. paused/withdraw_only는 네트워크 지원 정보와 별도로 유지한다.
unsupported 또는 알려지지 않은 입출금 상태는 직접 입금 경로로 확정하지 않는다.

`bridge_candidate`는 그 거래소로 직접 입금하는 체인이 확인되지 않은 후보다. 실행은 허용하지 않는다.
한 거래소에서 tradable인 토큰이 다른 거래소에서는 bridge_candidate일 수 있다.
후속 UI에서는 어느 거래소에서도 tradable이 아닌 후보를 별도 섹션에 표시한다.

`excluded`는 수동 제외나 컨트랙트 식별 충돌 등으로 배제된 경로다.
컨트랙트를 확정하지 못한 종목, 비EVM 네이티브, OKX 미인식 토큰은 거래 목록용 토큰을 생성하지 않고 리포트에 기록한다.
XRP 등 비EVM 코인의 EVM 페깅 토큰이 CoinGecko coin_id에 연결돼 있으면 브릿지 후보로 유지한다.

M1 토큰의 `execution_enabled`는 모두 false다.

## 식별과 체인 매핑

- 거래소별 티커의 `coin_id`를 기준으로 식별한다. 전체 코인 목록의 symbol만으로 검색해 확정하지 않는다.
- 같은 티커에 여러 ID가 있으면 자동 선택하지 않는다. 거래소마다 ID가 다르면 서로 다른 코인으로 유지한다.
- 같은 (체인, 주소)를 서로 다른 코인 ID가 주장하면 관련 경로를 모두 제외한다.
- CoinGecko `chain_identifier`와 OKX Market·Swap 지원 체인의 교집합으로 EVM 범위를 정한다.
- `config/chain_map.toml`의 거래소별 정확한 net_type만 사용한다. network_name은 표시·수동 검증 근거이며 런타임 식별 키가 아니다.
- .env에 있는 RPC의 개수는 레지스트리 조회 범위를 제한하지 않는다.

네트워크 매핑 예시:

```toml
[[networks]]
exchange = "upbit"
net_type = "ETH"
chain_index = "1"
platform_id = "ethereum"
evidence = "거래소 wallet 응답과 CoinGecko 플랫폼 ID를 확인한 근거"
```

새로 추가한 매핑은 실제 플랫폼의 chain_identifier와 일치해야 한다.
파일에 없는 net_type은 리포트 상단에 경고하고 해당 네트워크를 직접 입금 경로로 채택하지 않는다.
현재 파일은 실제 조회에서 확인한 별칭을 담는다. 비EVM·미지원 체인까지 임의로 EVM에 연결하지 않는다.

## 수동 오버라이드

`config/overrides.toml`을 수정한다. 아래는 형식 예시이며 실제 추가 시 근거를 확인한다.

```toml
[[mappings]]
exchange = "upbit"
symbol = "AI"
coin_id = "gensyn"
reason = "거래소 상장 정보와 검증된 CoinGecko ID"

[[tokens]]
coin_id = "ethereum"
chain_index = "1"
address = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
native = true
reason = "CoinGecko native_coin_id와 OKX 공식 네이티브 주소"

[[exclusions]]
coin_id = "example-coin-id"
exchange = "bithumb"
reason = "매핑 재검토"
```

실제 파일의 빈 `mappings = []` 또는 `exclusions = []` 선언에 테이블을 추가할 때는 해당 빈 선언을 먼저 제거한다.
tokens는 같은 coin_id·체인의 CA를 대체한다. exclusions의 exchange와 chain_index를 생략하면 해당 코인의 모든 경로에 적용된다.
중복된 매핑/토큰 오버라이드, 알 수 없는 필드, `allow_swap` 또는 강제 status 지정은 거부한다.
매핑을 고쳐도 거래소의 지원 네트워크를 우회해 tradable로 만들 수 없다.

OKX의 일반 EVM 네이티브 주소는 0xeeee…eeee이지만 **Arc USDC는 0x3600…0000 / decimals 6**으로 별도 처리한다.
네이티브 등록은 해당 체인의 CoinGecko native_coin_id와 대조하고 OKX 메타데이터도 검증한다.
Merlin의 native_coin_id는 wrapped-bitcoin으로 제공되어 별도 검증 전까지 네이티브 오버라이드를 추가하지 않았다.

## 재현성

실시간 목록과 입출금 상태는 바뀔 수 있다. 같은 입력·설정에 대해서는 JSON과 Markdown이 바이트 단위로 동일하며,
생성 시각 같은 변동 값을 산출물에 넣지 않는다. 입력 지문(source_digest)을 남긴다.

```sh
uv run scripts/build_registry.py --save-sources data/registry_sources.json --save-metadata data/registry_metadata.json
uv run scripts/build_registry.py --sources data/registry_sources.json --metadata data/registry_metadata.json --output-dir /tmp/dexdda-registry-replay
```

두 스냅샷을 모두 지정하면 API·.env 조회 없이 재현한다. 스냅샷은 명시적으로 지정한 경우에만 재사용하며, 자동으로 오래된 데이터를 최신 결과로 대체하지 않는다.
스냅샷에는 정규화한 공개 종목·네트워크·메타데이터만 저장하며 인증 헤더와 계정 입금 주소는 저장하지 않는다.
`--output-dir`, `--chain-map`, `--overrides`, `--settings`, `--env-file`도 지원한다.

## 호출량과 제한

`[registry]` 설정으로 CoinGecko 호출 간격, OKX 호출 간격, basic-info 배치를 조정한다.
기본 간격은 각각 2.2초와 1.1초, 메타데이터 배치는 20개다.
배치 20개는 실제 동작을 검증한 운용값이며 공식 최대 개수라는 주장이 아니다.
호출 제한은 간격을 둬 최대 3번까지 시도하고, 그 뒤에는 실패한다. 자동 유료 전환은 없다.

CoinGecko ticker 페이지는 `order=base_target`으로 빈 페이지까지 읽는다.
동일 페이지 반복이나 페이지 상한 초과는 전체 수집으로 간주하지 않고 실패한다.
