"""Server-owned prepared intents. Client confirmation cannot supply transaction data."""

from typing import Literal

from pydantic import Field

from app.config import Model


class ConfirmRequest(Model):
    intent_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirmed: Literal[True]


class SwapError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status
