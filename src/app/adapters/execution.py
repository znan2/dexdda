"""EVM transaction support. Broadcast is explicitly disabled by DRY_RUN."""

import re
from decimal import Decimal, localcontext
from typing import ClassVar
from urllib.parse import urlencode

from eth_account import Account
from eth_utils import to_checksum_address

from app.adapters.base import AdapterError, Failure, classify, request_json
from app.adapters.detail import ReadRpc, parse_quote, uint
from app.adapters.okx import BASE_URL, auth_headers
from app.registry.models import EVM_ADDRESS
from app.swap.models import SwapError


def address(value):
    if (
        not isinstance(value, str)
        or not EVM_ADDRESS.fullmatch(value.lower())
        or int(value, 16) == 0
    ):
        raise AdapterError(Failure.SCHEMA)
    return value.lower()


def hexdata(value, *, size=None):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise AdapterError(Failure.SCHEMA)
    if len(value) > 300002 or (size is not None and len(value) != 2 + size * 2):
        raise AdapterError(Failure.SCHEMA)
    return value.lower()


class ExecutionRpc(ReadRpc):
    READ_METHODS: ClassVar = {
        "eth_getBalance",
        "eth_getTransactionCount",
        "eth_getTransactionReceipt",
        "eth_getTransactionByHash",
        "eth_estimateGas",
        "eth_getCode",
    }

    def __init__(self, client, url, chain_id, *, dry_run):
        super().__init__(client, url, chain_id)
        self.dry_run = dry_run

    async def raw(self, method, params):
        if method == "eth_sendRawTransaction" and self.dry_run:
            raise SwapError("dry_run_broadcast_blocked", "DRY_RUN에서는 전송할 수 없습니다.")
        if method not in self.READ_METHODS | {"eth_sendRawTransaction"}:
            raise AdapterError(Failure.CONFIG)
        payload = await request_json(
            self.client,
            "POST",
            self.url.get_secret_value(),
            check_api_error=False,
            json={"jsonrpc": "2.0", "id": 3, "method": method, "params": params},
        )
        if (
            not isinstance(payload, dict)
            or payload.get("jsonrpc") != "2.0"
            or type(payload.get("id")) is not int
            or payload["id"] != 3
        ):
            raise AdapterError(Failure.SCHEMA)
        if payload.get("error") is not None:
            raise AdapterError(Failure.RPC)
        return payload.get("result")

    async def call(self, method, params):
        if method in self.READ_METHODS:
            return await self.raw(method, params)
        return await super().call(method, params)

    async def token_balance(self, owner, token):
        return await self.integer(
            "eth_call",
            [{"to": address(token), "data": "0x70a08231" + address(owner)[2:].zfill(64)}, "latest"],
        )

    async def broadcast(self, raw, expected_hash):
        if self.dry_run:
            raise SwapError("dry_run_broadcast_blocked", "DRY_RUN에서는 전송할 수 없습니다.")
        actual = await self.raw("eth_sendRawTransaction", [raw])
        if hexdata(actual, size=32) != expected_hash:
            raise AdapterError(Failure.SCHEMA)
        return actual


class LocalSigner:
    def __init__(self, credentials):
        self.credentials = credentials

    def check(self, owner):
        try:
            account = Account.from_key(self.credentials.wallet_private_key.get_secret_value())
            if account.address.lower() != owner:
                raise ValueError()
        except Exception:  # noqa: BLE001 - never expose private key errors.
            raise SwapError(
                "wallet_key_mismatch", "개인키와 WALLET_ADDRESS가 일치하지 않습니다."
            ) from None

    def sign(self, transaction, owner):
        if self.credentials.dry_run:
            raise SwapError("dry_run_signing_blocked", "DRY_RUN에서는 서명할 수 없습니다.")
        self.check(owner)
        tx = {**transaction, "to": to_checksum_address(transaction["to"])}
        signed = Account.sign_transaction(
            tx, self.credentials.wallet_private_key.get_secret_value()
        )
        return "0x" + signed.raw_transaction.hex().removeprefix(
            "0x"
        ), "0x" + signed.hash.hex().removeprefix("0x")


class SwapAdapter:
    def __init__(self, detail_adapter, contracts):
        self.adapter, self.contracts = detail_adapter, contracts

    async def approval(self, asset, units):
        row = await self.adapter.okx(
            "approve-transaction",
            {
                "chainIndex": asset.chain_index,
                "tokenContractAddress": asset.address,
                "approveAmount": str(units),
            },
        )
        spender = address(row.get("dexContractAddress"))
        if self.contracts.get(asset.chain_index, {}).get("spender") != spender:
            raise SwapError(
                "spender_changed",
                "승인 컨트랙트가 검증된 목록과 다릅니다. 공식 변경 내용을 확인하세요.",
            )
        data = hexdata(row.get("data"))
        expected = "0x095ea7b3" + spender[2:].zfill(64) + hex(units)[2:].zfill(64)
        if data != expected:
            raise SwapError(
                "approval_payload_mismatch", "승인 대상 또는 금액이 요청과 일치하지 않습니다."
            )
        gas = uint(row.get("gasLimit"))
        if not 21000 <= gas <= 10_000_000:
            raise AdapterError(Failure.SCHEMA)
        return {"to": asset.address, "value": 0, "data": data, "gas": (gas * 3 + 1) // 2}, spender

    async def swap(self, token, asset, units, owner, slippage):
        row = await self.adapter.okx(
            "swap",
            {
                "chainIndex": token.chain_index,
                "amount": str(units),
                "fromTokenAddress": asset.address,
                "toTokenAddress": token.address,
                "userWalletAddress": owner,
                "swapReceiverAddress": owner,
                "slippagePercent": slippage,
                "swapMode": "exactIn",
                "autoSlippage": "false",
                "priceImpactProtectionPercent": "3",
            },
        )
        quote = parse_quote(row.get("routerResult"), token, asset, units)
        tx = row.get("tx")
        if not isinstance(tx, dict) or address(tx.get("from")) != owner:
            raise SwapError("transaction_sender_mismatch", "트랜잭션 발신 주소가 지갑과 다릅니다.")
        to = address(tx.get("to"))
        if to != self.contracts.get(token.chain_index, {}).get("router"):
            raise SwapError(
                "router_changed",
                "스왑 컨트랙트가 검증된 목록과 다릅니다. 공식 변경 내용을 확인하세요.",
            )
        if "chainId" in tx and uint(tx["chainId"]) != int(token.chain_index):
            raise AdapterError(Failure.CHAIN)
        if uint(tx.get("value")) != 0:
            raise SwapError(
                "unexpected_native_value",
                "스테이블코인 매수에서 예기치 않은 네이티브 코인 전송 값입니다.",
            )
        data = hexdata(tx.get("data"))
        if len(data) < 10:
            raise AdapterError(Failure.SCHEMA)
        gas = uint(tx.get("gas"))
        if not 21000 <= gas <= 10_000_000:
            raise AdapterError(Failure.SCHEMA)
        minimum = uint(tx.get("minReceiveAmount"))
        with localcontext() as ctx:
            ctx.prec = 400
            expected = int(quote["quantity"] * 10**token.decimals * (1 - Decimal(slippage) / 100))
        if minimum < expected or minimum == 0:
            raise SwapError(
                "slippage_mismatch", "트랜잭션 최소 수령량이 슬리피지 한도보다 낮습니다."
            )
        if "slippagePercent" in tx and Decimal(str(tx["slippagePercent"])) != Decimal(slippage):
            raise SwapError("slippage_mismatch", "응답 슬리피지가 설정과 다릅니다.")
        return {"to": to, "value": 0, "data": data, "gas": (gas * 3 + 1) // 2}, quote, minimum

    async def history(self, chain, tx_hash, owner, token):
        path = "/api/v6/dex/aggregator/history?" + urlencode(
            {"chainIndex": chain, "txHash": tx_hash}
        )

        async def operation():
            payload = await request_json(
                self.adapter.client,
                "GET",
                BASE_URL + path,
                headers=auth_headers(self.adapter.credentials, "GET", path),
            )
            if not isinstance(payload, dict) or str(payload.get("code")) != "0":
                raise AdapterError(classify(200, payload))
            row = payload.get("data")
            if isinstance(row, list) and len(row) == 1:
                row = row[0]
            if (
                not isinstance(row, dict)
                or str(row.get("chainIndex")) != chain
                or str(row.get("txHash", "")).lower() != tx_hash
            ):
                raise AdapterError(Failure.SCHEMA)
            if row.get("status") != "success":
                return None
            if address(row.get("toAddress")) != owner or address(row.get("fromAddress")) != owner:
                raise AdapterError(Failure.SCHEMA)
            outputs = row.get("toTokenDetails")
            if isinstance(outputs, dict):
                outputs = [outputs]
            if not isinstance(outputs, list):
                raise AdapterError(Failure.SCHEMA)
            amounts = []
            for output in outputs:
                if not isinstance(output, dict):
                    raise AdapterError(Failure.SCHEMA)
                if str(output.get("tokenAddress", "")).lower() == token.address:
                    value = Decimal(str(output.get("amount")))
                    if (
                        not value.is_finite()
                        or value < 0
                        or value != value.to_integral_value()
                        or value >= 2**256
                    ):
                        raise AdapterError(Failure.SCHEMA)
                    amounts.append(value)
            if not amounts:
                raise AdapterError(Failure.SCHEMA)
            with localcontext() as ctx:
                ctx.prec = 400
                return format(sum(amounts) / 10**token.decimals, "f")

        return await self.adapter.okx_gate.run(operation)
