import base64
import hashlib
import hmac
from uuid import UUID

import jwt
import pytest

from app.adapters import bithumb, okx, upbit


def test_okx_signature_covers_exact_query_and_body(credentials):
    timestamp = "2026-09-19T00:00:00.123Z"
    path = "/example?chainIndex=1&amount=1000"
    body = '{"a":1}'
    headers = okx.auth_headers(credentials, "post", path, body, timestamp)
    # Independent reconstruction of the signed bytes, including query order and raw body.
    expected = base64.b64encode(
        hmac.digest(
            credentials.okx_secret_key.get_secret_value().encode(),
            b'2026-09-19T00:00:00.123ZPOST/example?chainIndex=1&amount=1000{"a":1}',
            hashlib.sha256,
        )
    ).decode()
    assert headers["OK-ACCESS-SIGN"] == expected
    assert headers["OK-ACCESS-TIMESTAMP"] == timestamp
    assert headers["OK-ACCESS-KEY"] == credentials.okx_api_key.get_secret_value()
    assert headers["OK-ACCESS-PASSPHRASE"] == credentials.okx_passphrase.get_secret_value()


@pytest.mark.parametrize(
    "module,prefix,algorithm",
    [
        (upbit, "upbit", "HS512"),
        (bithumb, "bithumb", "HS256"),
    ],
)
def test_jwt_algorithm_nonce_and_payload(credentials, monkeypatch, module, prefix, algorithm):
    monkeypatch.setattr(bithumb.time, "time", lambda: 1789776000.123)
    tokens = [
        module.auth_headers(credentials)["Authorization"].removeprefix("Bearer ") for _ in range(2)
    ]
    secret = getattr(credentials, prefix + "_secret_key").get_secret_value()
    claims = [jwt.decode(token, secret, algorithms=[algorithm]) for token in tokens]
    assert jwt.get_unverified_header(tokens[0])["alg"] == algorithm
    assert (
        claims[0]["access_key"] == getattr(credentials, prefix + "_access_key").get_secret_value()
    )
    assert UUID(claims[0]["nonce"]).version == 4
    assert claims[0]["nonce"] != claims[1]["nonce"]
    expected_fields = {"access_key", "nonce"}
    if prefix == "bithumb":
        expected_fields.add("timestamp")
        assert claims[0]["timestamp"] == 1789776000123
        assert type(claims[0]["timestamp"]) is int
    assert set(claims[0]) == expected_fields


def test_upbit_provider_issued_key_length_keeps_hs512(credentials, recwarn):
    from jwt.warnings import InsecureKeyLengthWarning
    from pydantic import SecretStr

    short_credentials = credentials.model_copy(update={"upbit_secret_key": SecretStr("x" * 40)})
    token = upbit.auth_headers(short_credentials)["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token)["alg"] == "HS512"
    assert not any(issubclass(w.category, InsecureKeyLengthWarning) for w in recwarn)
