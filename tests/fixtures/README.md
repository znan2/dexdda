# 합성 API 응답

실제 계정 응답을 저장하지 않는다. responses.json은 공식 스키마에 맞춘 합성 데이터이며 주소는 테스트 전용 문자열이다.

- [OKX 체인](https://web3.okx.com/onchainos/dev-docs/trade/dex-get-aggregator-supported-chains)
- [업비트 입금 주소](https://docs.upbit.com/kr/reference/list-deposit-addresses)
- [빗썸 입금 주소](https://apidocs.bithumb.com/reference/전체-입금-주소-조회)
- [CoinGecko ping](https://docs.coingecko.com/demo/reference/ping-server)
- [Ethereum eth_chainId](https://ethereum.org/en/developers/docs/apis/json-rpc/#eth_chainid)

## M1 registry_sources.json

공식 스키마 기반 합성 데이터다. Gensyn의 Ethereum CA만 실제 공개 CoinGecko 응답에서 확인한 0x4d7078ddd6ccfed2f85db5b7d3ff16828d378d48을 고정한다.
Artificial Inu, Base/BSC의 예시 CA와 기타 토큰 메타데이터는 동명·다중 체인 분기 테스트를 위한 합성값이다. 실제 계정 응답은 사용하지 않는다.
Arc 예외 주소는 OKX 공식 네이티브 FAQ에 근거한다.
