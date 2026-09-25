import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from scalar_fastapi import get_scalar_api_reference

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.http_security import HttpSecurityMiddleware
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.router import router as auth_router
from app.modules.holdings.router import router as holdings_router
from app.modules.filiales.router import router as filiales_router
from app.modules.roles.router import router as roles_router
from app.modules.users.router import router as users_router
from app.modules.clients.router import router as clients_router
from app.modules.service_orders.router import router as service_orders_router
from app.modules.inspections.router import router as inspections_router
from app.modules.parts.router import router as parts_router
from app.modules.post_ventas.router import router as post_ventas_router
from app.modules.ajustes.router import router as ajustes_router
from app.modules.vehicle_catalog.router import router as vehicle_catalog_router
from app.modules.warehouse.router import router as almacen_router
from app.modules.concesionario.router import router as concesionario_router
from app.modules.administracion.router import router as administracion_router
from app.modules.compras.router import router as compras_router
from app.modules.kpis.router import router as kpis_router
from app.modules.exchange_rates.router import router as exchange_rates_router
from app.modules.exchange_rates.service import ExchangeRateService

logger = logging.getLogger(__name__)
settings = get_settings()

BCV_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60


async def _refresh_bcv_rate_loop() -> None:
    """Keeps `exchange_rates` current without relying on a host-level cron —
    the production deployment is a single container with nothing else able
    to run scripts.update_bcv_rates on a schedule."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await ExchangeRateService(session).refresh()
        except Exception:
            logger.exception("BCV rate refresh failed")
        await asyncio.sleep(BCV_REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_refresh_bcv_rate_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=settings.app_name,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(HttpSecurityMiddleware)

register_exception_handlers(app)

app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(holdings_router, prefix=settings.api_v1_prefix)
app.include_router(filiales_router, prefix=settings.api_v1_prefix)
app.include_router(roles_router, prefix=settings.api_v1_prefix)
app.include_router(users_router, prefix=settings.api_v1_prefix)
app.include_router(clients_router, prefix=settings.api_v1_prefix)
app.include_router(service_orders_router, prefix=settings.api_v1_prefix)
app.include_router(inspections_router, prefix=settings.api_v1_prefix)
app.include_router(parts_router, prefix=settings.api_v1_prefix)
app.include_router(post_ventas_router, prefix=settings.api_v1_prefix)
app.include_router(ajustes_router, prefix=settings.api_v1_prefix)
app.include_router(vehicle_catalog_router, prefix=settings.api_v1_prefix)
app.include_router(almacen_router, prefix=settings.api_v1_prefix)
app.include_router(concesionario_router, prefix=settings.api_v1_prefix)
app.include_router(administracion_router, prefix=settings.api_v1_prefix)
app.include_router(compras_router, prefix=settings.api_v1_prefix)
app.include_router(kpis_router, prefix=settings.api_v1_prefix)
app.include_router(exchange_rates_router, prefix=settings.api_v1_prefix)

Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
app.mount(
    f"{settings.api_v1_prefix}/uploads",
    StaticFiles(directory=settings.uploads_dir),
    name="uploads",
)


@app.get("/api/v1/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Basic liveness check used by monitoring and local development."""
    return {"status": "ok"}


@app.get("/scalar", include_in_schema=False)
async def scalar_docs():
    """Serve interactive API documentation using Scalar instead of Swagger UI."""
    return get_scalar_api_reference(openapi_url=app.openapi_url, title=app.title)
