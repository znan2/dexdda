"""Wire validation with synthetic API payloads; no real keys or network."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from eth_account import Account
from test_detail import quote_payload
from test_pricing import token

from app.adapters.base import AdapterError
from app.adapters.execution import LocalSigner, SwapAdapter
from app.adapters.prices import Gate
from app.config import Credentials
from app.detail.models import PurchaseAsset
from app.swap.models import SwapError

OWNER = "0x" + "a" * 40
SPENDER = "0x" + "b" * 40
ROUTER = "0x" + "d" * 40
ASSET = PurchaseAsset(
    chain_index="1",
    address="0x" + "c" * 40,
    decimals=6,
    symbol="USDT",
    evidence="synthetic fixture",
)
UNITS = 1_000_000_000
CONTRACTS = {"1": {"spender": SPENDER, "router": ROUTER}}


class Provider:
    def __init__(self, row):
        self.row = row
        self.requests = []

    async def okx(self, endpoint, params):
        self.requests.append((endpoint, params))
        return deepcopy(self.row)


def swap_payload():
    quote = quote_payload()
    quote["toToken"]["taxRate"] = "0"
    return {
        "routerResult": quote,
        "tx": {
            "from": OWNER,
            "to": ROUTER,
            "chainId": "1",
            "value": "0",
            "data": "0x12345678",
            "gas": "100000",
            "minReceiveAmount": "12283",
            "slippagePercent": "0.5",
        },
    }


async def test_unsigned_swap_checks_units_owner_and_slippage():
    provider = Provider(swap_payload())
    tx, quote, minimum = await SwapAdapter(provider, CONTRACTS).swap(
        token(), ASSET, UNITS, OWNER, "0.5"
    )
    assert tx["gas"] == 150000 and tx["value"] == 0 and minimum == 12283
    assert quote["quantity"] == Decimal("0.000000000000012345")
    params = provider.requests[0][1]
    assert params["swapReceiverAddress"] == OWNER
    assert params["amount"] == str(UNITS) and params["slippagePercent"] == "0.5"


@pytest.mark.parametrize(
    "field,value",
    [
        ("from", "0x" + "e" * 40),
        ("to", "0x" + "e" * 40),
        ("chainId", "56"),
        ("value", "1"),
        ("data", "0x123"),
        ("gas", "20000"),
        ("minReceiveAmount", "12282"),
        ("slippagePercent", "1"),
    ],
)
async def test_unsigned_swap_rejects_tampered_fields(field, value):
    row = swap_payload()
    row["tx"][field] = value
    with pytest.raises((AdapterError, SwapError)):
        await SwapAdapter(Provider(row), CONTRACTS).swap(token(), ASSET, UNITS, OWNER, "0.5")


@pytest.mark.parametrize("changed", ["none", "spender", "amount", "selector"])
async def test_approval_payload_is_exact_and_finite(changed):
    data = "0x095ea7b3" + SPENDER[2:].zfill(64) + hex(UNITS)[2:].zfill(64)
    row = {"dexContractAddress": SPENDER, "data": data, "gasLimit": "65000"}
    if changed == "spender":
        row["dexContractAddress"] = "0x" + "e" * 40
    elif changed == "amount":
        row["data"] = data[:-64] + hex(2**256 - 1)[2:]
    elif changed == "selector":
        row["data"] = "0x12345678" + data[10:]
    service = SwapAdapter(Provider(row), CONTRACTS)
    if changed == "none":
        tx, spender = await service.approval(ASSET, UNITS)
        assert spender == SPENDER and tx["data"] == data and tx["gas"] == 97500
    else:
        with pytest.raises(SwapError):
            await service.approval(ASSET, UNITS)


@pytest.mark.parametrize("change", ["none", "list", "hash", "chain", "owner", "token", "fraction"])
async def test_history_identity_and_base_unit_precision(credentials, change):
    tx_hash = "0x" + "f" * 64
    row = {
        "chainIndex": "1",
        "txHash": tx_hash,
        "status": "success",
        "fromAddress": OWNER,
        "toAddress": OWNER,
        "toTokenDetails": {
            "tokenAddress": token().address,
            "amount": "123456789012345678901234567890.000",
        },
    }
    if change == "hash":
        row["txHash"] = "0x" + "e" * 64
    elif change == "chain":
        row["chainIndex"] = "56"
    elif change == "owner":
        row["toAddress"] = "0x" + "e" * 40
    elif change == "token":
        row["toTokenDetails"]["tokenAddress"] = "0x" + "e" * 40
    elif change == "fraction":
        row["toTokenDetails"]["amount"] = "1.2"
    if change == "list":
        row["toTokenDetails"] = [row["toTokenDetails"]]

    def handler(request):
        assert request.method == "GET" and request.url.params["txHash"] == tx_hash
        return httpx.Response(200, json={"code": "0", "data": [row] if change == "list" else row})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = SwapAdapter(
            SimpleNamespace(client=client, credentials=credentials, okx_gate=Gate(0)), CONTRACTS
        )
        if change in ("none", "list"):
            assert (
                await adapter.history("1", tx_hash, OWNER, token())
                == "123456789012.34567890123456789"
            )
        else:
            with pytest.raises(AdapterError):
                await adapter.history("1", tx_hash, OWNER, token())


def test_local_signer_recovers_synthetic_owner_and_rejects_mismatch():
    # Publicly known test-only scalar; never load .env or use a funded account.
    key = "0x" + "01".zfill(64)
    owner = Account.from_key(key).address.lower()
    signer = LocalSigner(Credentials(wallet_private_key=key, dry_run=False))
    raw, tx_hash = signer.sign(
        {
            "chainId": 1,
            "nonce": 0,
            "to": ROUTER,
            "value": 0,
            "gas": 21000,
            "gasPrice": 1,
            "data": "0x",
        },
        owner,
    )
    assert Account.recover_transaction(raw).lower() == owner and len(tx_hash) == 66
    with pytest.raises(SwapError, match="개인키"):
        signer.sign({"to": ROUTER}, OWNER)
