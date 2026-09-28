"""Local session-protected execution API; confirmations reference server-owned intents."""

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from app.adapters.base import AdapterError
from app.detail.models import DetailRequest
from app.detail.service import DetailUnavailable
from app.preferences import Change
from app.private_store import StoreError
from app.swap.models import ConfirmRequest, SwapError


def routes(application):
    application.state.csrf = secrets.token_urlsafe(32)

    def response(data, status=200):
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    def authorized(request):
        token = request.headers.get("X-Dexdda-Session", "")
        origin = request.headers.get("Origin")
        same_origin = not origin or origin.rstrip("/") == str(request.base_url).rstrip("/")
        return same_origin and secrets.compare_digest(token, application.state.csrf)

    async def invoke(operation):
        try:
            return response(await operation())
        except (SwapError, DetailUnavailable) as exc:
            return response({"error": exc.code, "message": str(exc)}, exc.status)
        except AdapterError as exc:
            return response({"error": exc.category.value, "message": str(exc)}, 503)
        except StoreError:
            return response(
                {
                    "error": "operation_busy_or_store_invalid",
                    "message": "진행 중 작업 또는 로컬 기록을 확인하세요.",
                },
                409,
            )
        except Exception:  # noqa: BLE001 - sanitize external data.
            return response(
                {
                    "error": "internal_error",
                    "message": "요청 처리에 실패했습니다. 비밀값 보호를 위해 원문은 표시하지 않습니다.",
                },
                503,
            )

    @application.get("/api/runtime")
    async def runtime():
        collector = application.state.collector
        swap = application.state.swap
        return response(
            {
                "csrf": application.state.csrf,
                "dry_run": collector.dry_run if collector else True,
                "swap_available": swap is not None,
                "settings": collector.settings.swap.model_dump() if collector else None,
            }
        )

    @application.get("/api/preferences")
    async def preferences():
        c = application.state.collector
        if c is None or application.state.startup_error:
            return response({"error": "collector_not_started"}, 503)
        return response({**c.preferences.public(), "catalog": c.preferences.catalog()})

    @application.post("/api/preferences")
    async def change_preferences(payload: Change, request: Request):
        if not authorized(request):
            return response({"error": "local_session_required"}, 403)
        c = application.state.collector
        if c is None or application.state.startup_error:
            return response({"error": "collector_not_started"}, 503)
        # Keep validation and storage errors sanitized without nesting responses.
        try:
            c.preferences.change(payload)
        except ValueError:
            return response({"error": "invalid_exclusion"}, 422)
        except StoreError:
            return response(
                {
                    "error": "preferences_not_saved",
                    "message": "저장에 실패했습니다. 기존 설정을 유지합니다.",
                },
                409,
            )
        except Exception:  # noqa: BLE001 - sanitize storage failures.
            return response(
                {
                    "error": "preferences_not_saved",
                    "message": "저장에 실패했습니다. 기존 설정을 유지합니다.",
                },
                503,
            )
        return response(c.preferences.public())

    @application.post("/api/wallet-status/refresh")
    async def refresh_wallet_status(request: Request):
        if not authorized(request):
            return response({"error": "local_session_required"}, 403)
        c = application.state.collector
        if c is None or not c.running:
            return response({"error": "collector_not_started"}, 503)

        async def operation():
            await c.wallet_status.refresh()
            return {
                "wallet_status": {ex: c.wallet_status.status(ex) for ex in c.markets},
                "cooldown_seconds": 10,
            }

        return await invoke(operation)

    @application.post("/api/balances/refresh")
    async def refresh_balances(request: Request):
        if not authorized(request):
            return response({"error": "local_session_required"}, 403)
        c = application.state.collector
        if c is None or not c.running:
            return response({"error": "collector_not_started"}, 503)

        async def operation():
            await c.balances.refresh()
            return c.balances.snapshot()

        return await invoke(operation)

    @application.get("/api/operations")
    async def operations():
        service = application.state.operations
        return response(
            service.snapshot()
            if service
            else {
                "markets": {},
                "reference_fx": None,
                "reference_premium_percent": None,
                "does_not_affect_gap": True,
            }
        )

    @application.post("/api/swap/prepare")
    async def prepare(payload: DetailRequest, request: Request):
        if not authorized(request):
            return response({"error": "local_session_required"}, 403)
        if application.state.swap is None:
            return response({"error": "execution_not_started"}, 503)
        return await invoke(lambda: application.state.swap.prepare(payload))

    @application.post("/api/swap/confirm")
    async def confirm(payload: ConfirmRequest, request: Request):
        if not authorized(request):
            return response({"error": "local_session_required"}, 403)
        if application.state.swap is None:
            return response({"error": "execution_not_started"}, 503)
        return await invoke(lambda: application.state.swap.confirm(payload.intent_id))

    @application.get("/api/swaps")
    async def history():
        service = application.state.swap
        if service is None:
            return response({"items": []})
        try:
            entries = sorted(
                service.read()["intents"].values(), key=lambda e: e["created_at_ms"], reverse=True
            )
            return response({"items": [service.public(e) for e in entries[:30]]})
        except StoreError:
            return response({"items": [], "error": "history_unavailable"}, 503)

    @application.get("/api/swap/{intent_id}")
    async def status(intent_id: str):
        if application.state.swap is None:
            return response({"error": "execution_not_started"}, 503)
        return await invoke(lambda: application.state.swap.status(intent_id))

    @application.get("/api/swap/{intent_id}/completion")
    async def completion(intent_id: str):
        service, book = application.state.swap, application.state.address_book
        if service is None or book is None:
            return response({"error": "execution_not_started"}, 503)

        async def operation():
            status = await service.status(intent_id)
            if status["status"] != "success":
                raise SwapError(
                    "swap_not_successful",
                    "성공이 확인된 스왑만 입금 완료 화면을 열 수 있습니다.",
                    409,
                )
            entry = service.read()["intents"][intent_id]
            req = entry["request"]
            addresses = book.for_route(req["coin_id"], req["chain_index"], req["address"])
            for exchange in {a["exchange"] for a in addresses}:
                try:
                    wallets = await service.detail.adapters().wallets(exchange)
                except Exception:  # noqa: BLE001 - sanitize external data.
                    wallets = []
                for item in addresses:
                    if item["exchange"] != exchange:
                        continue
                    found = [
                        w
                        for w in wallets
                        if w["currency"] == item["currency"] and w["net_type"] == item["net_type"]
                    ]
                    item["wallet_state"] = found[0]["wallet_state"] if len(found) == 1 else None
                    item["deposit_possible"] = item["wallet_state"] in ("working", "deposit_only")
                    item["reference_only"] = exchange == "upbit"
                    item["network_name"] = (
                        found[0].get("network_name", item["net_type"])
                        if len(found) == 1
                        else item["net_type"]
                    )
            return {**status, "addresses": addresses, "manual_transfer_only": True}

        return await invoke(operation)
