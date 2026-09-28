# M1 검증 기록

**상태: 완료.**

## 실제 수집 및 빌드

`uv run scripts/build_registry.py --save-sources data/registry_sources.json --save-metadata data/registry_metadata.json`로 모든 공급자의 실제 데이터를 수집하고 산출물을 생성했다. 두 save 옵션은 재현용 스냅샷 보관만 추가하며, 기본 빌드의 범위와 산출물 경로는 같다.

- 업비트 KRW 마켓: 289개
- 빗썸 KRW 마켓: 480개
- 전체 769개 거래소별 마켓을 모두 대조: ID 매핑 766개, ID 미확정 3개. 누락 없이 레지스트리 또는 제외 리포트에 반영했다.
- 코인 ID: 482개 / 공통 EVM 체인: 29개
- OKX 조회 후보 914개 중 메타데이터 채택 912개
- 분류: {"tradable": 472, "bridge_candidate": 998, "excluded": 207, "native": 26}
- tradable/bridge_candidate/excluded는 거래소별 경로 수이며, 코인 수와 다르다. native는 코인·체인·주소 단위다.

## 완료 기준 대조

| 요구사항 | 근거 |
|---|---|
| 1. 두 거래소 KRW 목록·풀네임 | 실제 마켓 769개 전부 식별/누락 집계에 포함 |
| 2. CoinGecko 티커 전체 페이지 | stable order로 빈 페이지까지 수집, 반복 페이지 실패 테스트 |
| 3. coin_id와 CA 조인 | coins/list include_platform=true, gensyn CA 회귀 테스트 및 실제 확인 |
| 4. 입출금 네트워크 | 두 거래소 wallet API 필드 수집, 상태와 지원 여부 분리 |
| 5. 체인 정규화·OKX 교차 | 명시적 net_type 별칭과 플랫폼 ID 검증, 실제 공통 29개 EVM 체인 |
| 6. 거래소별 분류 | 다중 체인·중단/미지원·네이티브·같은 티커 다른 ID 테스트 |
| 7. OKX 배치 메타데이터 | 실제 20개 배치 수집, 이름/decimals/tag 반영, 미인식 CA 제외 |
| 8. 오버라이드 | ID 고정·CA 대체·네이티브·수동 제외 우선순위 및 실행 허용 금지 테스트 |
| 두 파일 생성·결정적 결과 | 실제 registry.json / registry_report.md 생성, 동일 스냅샷 재실행 바이트 일치 |
| AI가 Gensyn에 연결 | Ethereum 0x4d7078ddd6ccfed2f85db5b7d3ff16828d378d48, Artificial Inu 선택 금지 테스트 |
| 미매핑 경고 | 전체 KRW 마켓 기준 미매핑 집합을 리포트와 정확히 대조, 상단 경고 확인 |
| fixture 단위 테스트 | M0 포함 100 passed; 외부 API 호출 없이 실행 |

## 검증 명령

```sh
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run scripts/build_registry.py --sources data/registry_sources.json --metadata data/registry_metadata.json
uv run scripts/build_registry.py --sources data/registry_sources.json --metadata data/registry_metadata.json --output-dir /private/tmp/dexdda-m1-replay
```

테스트 100개·린트·포맷 검사 통과. API 오류가 발생했을 때 기존 산출물을 보존하는 동작과 비밀값 비노출도 테스트했다.

## 리포트에 남긴 데이터 확인 항목

- 미매핑 네트워크: 거래소별 net_type 189개. 비EVM·OKX 지원 교집합 밖의 체인도 포함하므로 전부 구현 오류를 뜻하지 않는다.
- ID 미확정: 빗썸 ARK, 업비트 TAO는 거래소 티커 ID 누락; 빗썸 PROS의 pharos-2는 코인 목록에 없다. 다른 거래소의 ID를 임의로 복사하지 않았다.
- OKX 미인식: Euler의 Sonic CA, PayPal USD의 Ethereum CA. 리포트에 주소를 명시하고 제외했다.
- AI는 양 거래소의 입금 네트워크가 GENSYN으로 조회돼, Ethereum 버전을 양쪽 모두 브릿지 후보로 분류한다.
- 후속 수집에서 상장 정보·CoinGecko 티커·입출금 상태가 바뀌면 결과도 바뀔 수 있다. 결정성은 같은 입력·설정에 대해 검증한다.

## 산출물 해시

입력 지문: `30a4c4617d68a73850b748918edd2008425618d12d2a536d748cd47a993998fc`

- registry.json: `cfed1121bb91ba7dff0dcdb0cb0620a219a1d56774f312d4111eb600140bc656`
- registry_report.md: `1fd186742e6ba932ca7035c9e29e73068479569f583803f2d8bdcc6ec80d404c`

M1은 조회와 레지스트리 구축까지 완료했다. 모든 실행 플래그는 false이며 M2는 시작하지 않았다.

검증 기록 시각: 2026-09-19T21:19:24+09:00
