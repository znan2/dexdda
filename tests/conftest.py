import json
from pathlib import Path

import pytest

from app.config import Credentials


@pytest.fixture
def responses():
    return json.loads((Path(__file__).parent / "fixtures/responses.json").read_text())


@pytest.fixture
def credentials():
    return Credentials(
        okx_api_key="sentinel-okx-key",
        okx_secret_key="sentinel-okx-secret",
        okx_passphrase="sentinel-okx-passphrase",
        upbit_access_key="sentinel-upbit-key",
        upbit_secret_key="sentinel-upbit-secret-" * 4,
        bithumb_access_key="sentinel-bithumb-key",
        bithumb_secret_key="sentinel-bithumb-secret-" * 4,
        coingecko_api_key="sentinel-coingecko-key",
        wallet_address="sentinel-wallet-address",
        wallet_private_key="",
        rpc_urls={"1": "https://rpc.invalid/sentinel-rpc-key"},
    )
