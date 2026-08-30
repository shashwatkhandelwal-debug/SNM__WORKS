from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import init_db_pool, close_db_pool
from routers.auth import router as auth_router
from routers.dashboard import router as dashboard_router
from routers.commercial import router as commercial_router
from routers.marketing import router as marketing_router
from routers.catalogue import router as catalogue_router
from routers.skus import router as skus_router
from routers.jobs import router as jobs_router
from routers.qc import router as qc_router
from routers.lab_tests import router as lab_tests_router
from routers.capa import router as capa_router
from routers.engineering import router as engineering_router
from routers.recipes import router as recipes_router
from routers.downtime import router as downtime_router
from routers.despatch import router as despatch_router
from routers.organisation import router as organisation_router
from routers.costing import router as costing_router
from routers.certificates import router as certificates_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Initializes the database connection pool on startup
    and cleanly closes connections on shutdown.
    """
    await init_db_pool()
    yield
    await close_db_pool()


app = FastAPI(
    title="SNM Works",
    description="Technical textiles works management system for Swadeshi Niwar Mills",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register routers
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(commercial_router)
app.include_router(marketing_router)
app.include_router(catalogue_router)
app.include_router(skus_router)
app.include_router(jobs_router)
app.include_router(qc_router)
app.include_router(lab_tests_router)
app.include_router(capa_router)
app.include_router(engineering_router)
app.include_router(recipes_router)
app.include_router(downtime_router)
app.include_router(despatch_router)
app.include_router(organisation_router)
app.include_router(costing_router)
app.include_router(certificates_router)


@app.get("/health")
async def health():
    """
    Health check endpoint returning application status.
    """
    return {"status": "ok"}