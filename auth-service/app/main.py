from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.endpoints.auth import router as auth_router
from app.db.session import close_db, init_db
from app.dummy.user import create_dummy_user
from shared_utils.sync_permissions import sync_permissions


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await sync_permissions()
    await create_dummy_user()
    try:
        yield
    finally:
        await close_db()


app = FastAPI(
    title="Auth Service",
    version="1.0.0",
    docs_url="/auth/docs",
    redoc_url="/auth/redoc",
    openapi_url="/auth/openapi.json",
    lifespan=lifespan,
)
app.include_router(auth_router)



@app.get("/health")
def health():
    return {"status": "ok", "service": "auth"}


@app.get("/auth/health")
def auth_health():
    return health()
