"""Pure deterministic registry construction. Identity is coin ID + chain + contract."""

import copy
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from app.config import ConfigError

from .loader import canonical_json
from .models import EVM_ADDRESS, NATIVE_ADDRESS, ChainMap, Overrides, Registry
from .sources import digest, normalize_sources

EXCHANGES = ("upbit", "bithumb")


def unique_index(rows, field):
    result = {}
    for row in rows:
        key = row[field]
        if key in result and result[key] != row:
            raise ConfigError("원본 데이터에 서로 다른 중복 식별자가 있습니다.")
        result[key] = row
    return result


@dataclass
class Draft:
    sources: dict
    chains: list[dict]
    coins: list[dict]
    issues: list[dict]
    config: dict

    def token_keys(self):
        return sorted(
            {(t["chain_index"], t["address"]) for c in self.coins for t in c["tokens"]},
            key=lambda k: (int(k[0]), k[1]),
        )


def prepare(sources: dict, chain_map: ChainMap, overrides: Overrides) -> Draft:
    sources = normalize_sources(sources)
    issues = []

    def issue(code, **details):
        issues.append({"code": code, **details})

    catalog = unique_index(sources["coins"], "id")
    platforms = unique_index(sources["platforms"], "id")
    market_chains = unique_index(sources["market_chains"], "chainIndex")
    swap_chains = unique_index(sources["swap_chains"], "chainIndex")
    # CoinGecko chain_identifier is the Chainlist EVM ID. Non-EVM platforms use null.
    eligible = {
        str(p["chain_identifier"])
        for p in platforms.values()
        if type(p["chain_identifier"]) is int and p["chain_identifier"] > 0
    }
    eligible &= set(market_chains) & set(swap_chains)
    # Provider testnets are never trading candidates.
    eligible = {c for c in eligible if "testnet" not in swap_chains[c]["chainName"].lower()}
    chains = [
        {
            "chain_index": c,
            "name": swap_chains[c]["chainName"],
            "platform_ids": sorted(
                p["id"] for p in platforms.values() if str(p["chain_identifier"]) == c
            ),
        }
        for c in sorted(eligible, key=int)
    ]

    network_map = {}
    for n in chain_map.networks:
        p = platforms.get(n.platform_id)
        if p is None or str(p["chain_identifier"]) != n.chain_index:
            raise ConfigError("chain_map의 platform_id와 CoinGecko 체인 ID가 일치하지 않습니다.")
        network_map[(n.exchange, n.net_type)] = n.chain_index
    fixes = {(m.exchange, m.symbol): m for m in overrides.mappings}
    groups = {}
    for exchange in EXCHANGES:
        tickers = defaultdict(set)
        for ticker in sources[exchange + "_tickers"]:
            if ticker["coin_id"]:
                tickers[ticker["base"].upper()].add(ticker["coin_id"])
        markets = unique_index(sources[exchange + "_markets"], "market")
        wallets = defaultdict(list)
        for wallet in sources[exchange + "_wallets"]:
            wallets[wallet["currency"].upper()].append(wallet)
        for market_id, market in sorted(markets.items()):
            if not market_id.startswith("KRW-"):
                continue
            symbol = market_id.removeprefix("KRW-")
            networks = sorted(
                {(r["net_type"], r["network_name"], r["wallet_state"]) for r in wallets[symbol]}
            )
            if not networks:
                issue("missing_wallet_networks", exchange=exchange, symbol=symbol)
            for net_type, network_name, _ in networks:
                if (exchange, net_type) not in network_map:
                    issue(
                        "unmapped_network",
                        exchange=exchange,
                        net_type=net_type,
                        network_name=network_name,
                        symbol=symbol,
                    )

            ids = sorted(tickers[symbol])
            fix = fixes.get((exchange, symbol))
            if len(ids) > 1:
                issue(
                    "ambiguous_coin_id",
                    exchange=exchange,
                    symbol=symbol,
                    coin_ids=ids,
                    resolved_by_override=fix is not None,
                )
            if fix:
                coin_id = fix.coin_id
                issue("mapping_override", exchange=exchange, symbol=symbol, coin_id=coin_id)
            elif len(ids) == 1:
                coin_id = ids[0]
            else:
                if not ids:
                    issue("missing_coin_id", exchange=exchange, symbol=symbol)
                continue
            coin = catalog.get(coin_id)
            if coin is None:
                issue("missing_coin_record", exchange=exchange, symbol=symbol, coin_id=coin_id)
                continue
            group = groups.setdefault(
                coin_id,
                {
                    "coin_id": coin_id,
                    "symbol": symbol,
                    "names": {"coingecko": coin["name"]},
                    "exchanges": {},
                    "tokens": [],
                    "source": "coingecko",
                    "excluded_reason": None,
                },
            )
            if fix:
                group["source"] = "override"
            # Coin IDs are grouped; equal symbols across different IDs are not merged.
            if exchange in group["exchanges"]:
                raise ConfigError(
                    "같은 거래소의 여러 종목이 한 coin_id에 연결됐습니다. 매핑을 확인하세요."
                )
            group["names"][exchange + "_ko"] = market["korean_name"]
            group["names"][exchange + "_en"] = market["english_name"]
            group["exchanges"][exchange] = {
                "symbol": symbol,
                "markets": [market_id],
                "net_types": sorted({n[0] for n in networks}),
                "networks": [
                    {"net_type": n, "network_name": name, "wallet_state": state}
                    for n, name, state in networks
                ],
            }

    for coin_id, group in sorted(groups.items()):
        coin = catalog[coin_id]
        group["symbol"] = (group["exchanges"].get("upbit") or group["exchanges"]["bithumb"])[
            "symbol"
        ]
        candidates = defaultdict(set)
        for platform_id, address in sorted(coin["platforms"].items()):
            if not address:
                continue
            platform = platforms.get(platform_id)
            chain = str(platform["chain_identifier"]) if platform else ""
            if chain not in eligible:
                issue("unsupported_platform", coin_id=coin_id, platform_id=platform_id)
                continue
            address = address.lower()
            if not EVM_ADDRESS.fullmatch(address) or address == NATIVE_ADDRESS:
                issue(
                    "invalid_contract", coin_id=coin_id, chain_index=chain, platform_id=platform_id
                )
                continue
            candidates[chain].add(address)
        token_fixes = {t.chain_index: t for t in overrides.tokens if t.coin_id == coin_id}
        for chain, fix in token_fixes.items():
            if chain not in eligible:
                issue("unsupported_override_chain", coin_id=coin_id, chain_index=chain)
                continue
            if fix.native and not any(
                str(p["chain_identifier"]) == chain and p["native_coin_id"] == coin_id
                for p in platforms.values()
            ):
                raise ConfigError(
                    "네이티브 오버라이드의 coin_id가 해당 체인의 네이티브와 다릅니다."
                )
            candidates[chain] = {fix.address.lower()}
        for chain, addresses in sorted(candidates.items(), key=lambda p: int(p[0])):
            if len(addresses) != 1:
                issue(
                    "conflicting_contracts",
                    coin_id=coin_id,
                    chain_index=chain,
                    addresses=sorted(addresses),
                )
                continue
            address = next(iter(addresses))
            fix = token_fixes.get(chain)
            statuses, reasons = {}, {}
            for exchange, listing in group["exchanges"].items():
                # A paused deposit still represents a supported network; status is retained separately.
                direct = any(
                    network_map.get((exchange, n["net_type"])) == chain
                    and n["wallet_state"] in ("working", "paused", "withdraw_only", "deposit_only")
                    for n in listing["networks"]
                )
                statuses[exchange] = "tradable" if direct else "bridge_candidate"
                for excluded in overrides.exclusions:
                    if (
                        excluded.coin_id == coin_id
                        and excluded.exchange in (None, exchange)
                        and excluded.chain_index in (None, chain)
                    ):
                        statuses[exchange] = "excluded"
                        reasons[exchange] = excluded.reason
            group["tokens"].append(
                {
                    "chain_index": chain,
                    "address": address,
                    "native": bool(fix and fix.native),
                    "source": "override" if fix else "coingecko",
                    "status": statuses,
                    "exclusion_reasons": reasons,
                }
            )
        if not group["tokens"]:
            group["excluded_reason"] = "no_supported_evm_contract"
            issue("excluded_coin", coin_id=coin_id, reason=group["excluded_reason"])

    owners = defaultdict(set)
    for group in groups.values():
        for token in group["tokens"]:
            if any(s != "excluded" for s in token["status"].values()):
                owners[(token["chain_index"], token["address"])].add(group["coin_id"])
    for group in groups.values():
        for token in group["tokens"]:
            coin_ids = owners[(token["chain_index"], token["address"])]
            if len(coin_ids) > 1:
                for exchange in token["status"]:
                    token["status"][exchange] = "excluded"
                    token["exclusion_reasons"][exchange] = "contract_identity_collision"
                issue(
                    "contract_identity_collision",
                    coin_id=group["coin_id"],
                    chain_index=token["chain_index"],
                    address=token["address"],
                    coin_ids=sorted(coin_ids),
                )
    return Draft(
        sources,
        chains,
        list(groups.values()),
        issues,
        {"chain_map": chain_map.model_dump(), "overrides": overrides.model_dump()},
    )


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def finalize(draft: Draft, metadata: list[dict], *, generated_at: str | None = None) -> Registry:
    # generated_at is the only non-deterministic field; callers fix it for repeatable builds.
    issues, coins = copy.deepcopy(draft.issues), copy.deepcopy(draft.coins)
    lookup = {}
    for row in metadata:
        key = (row["chainIndex"], row["tokenContractAddress"].lower())
        if key in lookup and lookup[key] != row:
            raise ConfigError("OKX 메타데이터에 서로 다른 중복 컨트랙트가 있습니다.")
        lookup[key] = row
    for coin in coins:
        accepted = []
        for token in coin["tokens"]:
            meta = lookup.get((token["chain_index"], token["address"]))
            decimal = meta.get("decimal") if meta else None
            valid_decimal = (
                isinstance(decimal, str)
                and decimal.isascii()
                and decimal.isdecimal()
                and 0 <= int(decimal) <= 255
            )
            if not meta or not valid_decimal or not meta["tokenName"] or not meta["tokenSymbol"]:
                issues.append(
                    {
                        "code": "okx_unrecognized_contract",
                        "coin_id": coin["coin_id"],
                        "chain_index": token["chain_index"],
                        "address": token["address"],
                        "exchanges": sorted(token["status"]),
                    }
                )
                continue
            token.update(
                decimals=int(decimal),
                name=meta["tokenName"],
                symbol=meta["tokenSymbol"],
                community_recognized=meta["tagList"]["communityRecognized"],
            )
            accepted.append(token)
        coin["tokens"] = sorted(accepted, key=lambda t: (int(t["chain_index"]), t["address"]))
        if accepted:
            coin["names"]["okx"] = accepted[0]["name"]
        elif coin["excluded_reason"] is None:
            coin["excluded_reason"] = "okx_unrecognized_contract"
    # Deduplicate and sort diagnostics independently of source ordering.
    issues = [json.loads(s) for s in sorted({canonical_json(i) for i in issues})]
    content = {
        "source_digest": digest(
            {
                "sources": draft.sources,
                "config": draft.config,
                "metadata": sorted(metadata, key=canonical_json),
            }
        ),
        "generated_at": generated_at or utc_now(),
        "source_markets": {
            ex: sorted(
                r["market"]
                for r in draft.sources[ex + "_markets"]
                if r["market"].startswith("KRW-")
            )
            for ex in EXCHANGES
        },
        "chains": draft.chains,
        "coins": sorted(coins, key=lambda c: (c["symbol"], c["coin_id"])),
        "issues": issues,
    }
    return Registry.model_validate(content)


def counts(registry: Registry) -> dict:
    result = {"tradable": 0, "bridge_candidate": 0, "excluded": 0, "native": 0}
    for coin in registry.coins:
        rejected = sum(
            len(i["exchanges"])
            for i in registry.issues
            if i["code"] == "okx_unrecognized_contract" and i["coin_id"] == coin.coin_id
        )
        result["excluded"] += rejected
        if not coin.tokens and not rejected:
            result["excluded"] += len(coin.exchanges)
        for token in coin.tokens:
            result["native"] += int(token.native)
            for status in token.status.values():
                result[status] += 1
    result["excluded"] += sum(
        i["code"] in ("missing_coin_id", "missing_coin_record")
        or (i["code"] == "ambiguous_coin_id" and not i["resolved_by_override"])
        for i in registry.issues
    )
    return result


def report(registry: Registry) -> str:
    def cell(value):
        return escape(str(value)).replace("|", "\\|").replace("\n", " ")

    missing = [i for i in registry.issues if i["code"] == "unmapped_network"]
    lines = ["# 레지스트리 빌드 리포트", ""]
    if missing:
        lines += [
            f"> 경고: 미매핑 net_type {len({(i['exchange'], i['net_type']) for i in missing})}개. "
            + "이 네트워크는 직접 입금 경로로 확정하지 않았습니다.",
            "",
        ]
    lines += [
        "종목 식별은 CoinGecko 거래소 티커의 coin_id와 체인·컨트랙트를 사용합니다.",
        "tradable은 네트워크 지원 분류입니다. 현재 입금 가능·스왑 실행 준비 완료를 뜻하지 않습니다.",
        "M1의 모든 항목은 실행 비활성 상태입니다. 브릿지 후보는 조회만 제공합니다.",
        "",
        f"입력 지문: {registry.source_digest}",
        "",
        "## 집계",
        "",
        f"- 코인 ID: {len(registry.coins)}개",
        f"- Market·Swap 공통 EVM 체인: {len(registry.chains)}개",
        "- tradable/bridge_candidate/excluded는 거래소별 토큰 경로 수입니다.",
        "- 토큰이 없는 코인 및 식별 실패 종목도 거래소별 excluded에 포함합니다.",
        "- native는 거래소 중복을 제거한 코인·체인·주소 수입니다.",
        "",
        "| 분류 | 개수 |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in counts(registry).items()]
    lines += ["", "## 체인 범위", "", "| 체인 ID | 이름 | CoinGecko 플랫폼 |", "|---|---|---|"]
    lines += [
        f"| {c['chain_index']} | {cell(c['name'])} | {cell(', '.join(c['platform_ids']))} |"
        for c in registry.chains
    ]
    sections = [
        (
            "미매핑 net_type",
            {"unmapped_network"},
            ["exchange", "net_type", "network_name", "symbol"],
        ),
        (
            "CoinGecko ID 누락",
            {"missing_coin_id", "missing_coin_record"},
            ["exchange", "symbol", "coin_id"],
        ),
        (
            "티커의 다중 coin_id",
            {"ambiguous_coin_id"},
            ["exchange", "symbol", "coin_ids", "resolved_by_override"],
        ),
        ("OKX 미인식 CA", {"okx_unrecognized_contract"}, ["coin_id", "chain_index", "address"]),
        (
            "기타 제외·오버라이드·확인 항목",
            None,
            ["code", "coin_id", "exchange", "symbol", "chain_index", "platform_id", "reason"],
        ),
    ]
    covered = set()
    for title, codes, columns in sections:
        selected = (
            [i for i in registry.issues if i["code"] in codes]
            if codes
            else [i for i in registry.issues if i["code"] not in covered]
        )
        if codes:
            covered |= codes
        lines += ["", f"## {title}", ""]
        if not selected:
            lines.append("없음")
            continue
        lines += [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        lines += ["| " + " | ".join(cell(i.get(c, "")) for c in columns) + " |" for i in selected]
    return "\n".join(lines) + "\n"
