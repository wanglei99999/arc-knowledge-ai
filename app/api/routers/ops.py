from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.infrastructure.health import build_readiness_service
from app.services.readiness_service import ReadinessReport

router = APIRouter(tags=["operations"])


async def get_readiness_report() -> ReadinessReport:
    return await build_readiness_service().check()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@router.get("/ready")
async def ready() -> JSONResponse:
    report = await get_readiness_report()
    status_code = 503 if report.status == "unhealthy" else 200
    return JSONResponse(report.to_dict(), status_code=status_code)
