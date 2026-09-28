"""Local M5–M7 service; execution requires a server-owned confirmation. All external collection runs in the lifespan."""

from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.execution import routes as execution_routes
from app.config import PROJECT_ROOT, ConfigError, load_credentials, load_settings
from app.deposit.addresses import AddressBook
from app.detail.models import DetailRequest
from app.detail.service import DetailService, DetailUnavailable
from app.operations import Operations
from app.pricing.collector import Collector
from app.registry.loader import load_registry, registry_time
from app.swap.service import SwapService


def create_app(
    collector=None,
    detail=None,
    swap=None,
    operations=None,
    address_book=None,
    enable_execution=True,
    enable_operations=True,
):
    @asynccontextmanager
    async def lifespan(application):
        service = collector
        try:
            if service is None:
                registry_path = PROJECT_ROOT / "data/registry.json"
                service = Collector(
                    load_registry(registry_path),
                    load_settings(),
                    load_credentials(),
                    preferences_path=PROJECT_ROOT / "data/dashboard_preferences.json",
                    logos_path=PROJECT_ROOT / "data/logos.json",
                )
                application.state.registry_time = registry_time(service.registry, registry_path)
            application.state.collector = service
            await service.start()
            application.state.detail = detail or DetailService(service)
            if service.client is not None:
                if enable_execution:
                    application.state.swap = swap or SwapService(service, application.state.detail)
                application.state.address_book = address_book or AddressBook(
                    PROJECT_ROOT / "data/deposit_addresses.json",
                    service.credentials,
                    service.registry,
                    application.state.detail.chain_map,
                )
                if enable_operations:
                    application.state.operations = operations or Operations(
                        service, listing_path=PROJECT_ROOT / "data/listing_state.json"
                    )
                    application.state.operations.start()
        except ConfigError:
            application.state.startup_error = "invalid_config"
        except Exception:  # noqa: BLE001 - isolate workers and sanitize exceptions.
            application.state.startup_error = "internal_error"
        try:
            yield
        finally:
            if application.state.swap:
                await application.state.swap.stop()
            if application.state.operations:
                await application.state.operations.stop()
            if service:
                await service.stop()
            application.state.collector = None

    application = FastAPI(
        title="dexdda", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    application.state.swap = swap
    application.state.operations = operations
    application.state.address_book = address_book
    application.state.detail = detail
    application.state.collector = collector
    application.state.startup_error = None
    # (ISO 8601 UTC, "field" | "mtime") once the registry file is loaded; injected collectors
    # (tests, fixture apps) fall back to the registry's own field or report nothing.
    application.state.registry_time = None
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    application.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "src/app/web"), name="assets")

    def registry_health():
        # Health must answer even when startup failed and the collector is only partially built.
        service = application.state.collector
        registry = getattr(service, "registry", None)
        generated_at, source = application.state.registry_time or (None, None)
        if generated_at is None and registry is not None:
            generated_at, source = registry_time(registry)
        age_hours = stale = None
        if generated_at is not None and registry is not None:
            built = datetime.fromisoformat(generated_at).timestamp()
            age_hours = round((service.clock.milliseconds() / 1000 - built) / 3600, 3)
            stale = age_hours > service.settings.registry.max_age_hours
        return {
            "registry_generated_at": generated_at,
            "registry_age_hours": age_hours,
            "registry_time_source": source,
            "registry_stale": bool(stale),
        }

    @application.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "milestone": "M7",
            "execution_enabled": bool(
                application.state.swap
                and application.state.collector
                and not application.state.collector.credentials.dry_run
            ),
            **registry_health(),
        }

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(
            {"error": "invalid_request", "execution_enabled": False}, status_code=422
        )

    @application.post("/api/detail")
    async def detailed_quote(request: DetailRequest):
        service = application.state.detail
        if application.state.startup_error or service is None:
            return JSONResponse({"error": "detail_not_started"}, status_code=503)
        try:
            result = await service.calculate(request)
        except DetailUnavailable as exc:
            return JSONResponse(
                {"error": exc.code, "execution_enabled": False}, status_code=exc.status
            )
        except Exception:  # noqa: BLE001 - sanitize all provider exceptions.
            return JSONResponse(
                {"error": "internal_error", "execution_enabled": False}, status_code=500
            )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @application.get("/api/purchase-assets")
    async def purchase_assets():
        service = application.state.detail
        return JSONResponse(
            {"assets": service.asset_catalog() if service else []},
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/balances")
    async def balances():
        service = application.state.collector
        return JSONResponse(
            service.balances.snapshot() if service else {"chains": []},
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/logos")
    async def logos():
        service = application.state.collector
        return JSONResponse(
            service.logos.public() if service else {}, headers={"Cache-Control": "no-store"}
        )

    @application.get("/api/gaps")
    async def gaps():
        service = application.state.collector
        if application.state.startup_error or service is None:
            return JSONResponse(
                {
                    "error": application.state.startup_error or "collector_not_started",
                    "execution_enabled": False,
                },
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(service.snapshot(), headers={"Cache-Control": "no-store"})

    @application.get("/")
    async def index():
        return FileResponse(PROJECT_ROOT / "src/app/web/index.html")

    execution_routes(application)
    return application


app = create_app()


def main():
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc))
        raise SystemExit(2) from None
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, access_log=False)
