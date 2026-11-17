from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import ensure_directories
from app.database import Base, SessionLocal, engine, ensure_additive_schema
from app.routers import auth, calendar, ecourts, home, law, matters, memos, system, tasks
from app.services.seed_service import ensure_seed_data


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_directories()
    Base.metadata.create_all(bind=engine)
    ensure_additive_schema(engine)
    with SessionLocal() as db:
        ensure_seed_data(db)
    yield


app = FastAPI(
    title="AMI API",
    description="Private Legal Office Management System Backend (Phase 4)",
    version="1.3.0-phase4",
    lifespan=lifespan,
)

# Local development CORS (desktop / simulator / local web testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "app": "AMI", "phase": 4, "message": "AMI Legal Backend is live"}


@app.get("/api/health")
def health_check():
    return {"status": "ok", "app": "AMI", "phase": 4}


app.include_router(auth.router)
app.include_router(auth.users_router)
app.include_router(home.router)
app.include_router(calendar.router)
app.include_router(matters.router)
app.include_router(tasks.router)
app.include_router(memos.router)
app.include_router(law.router)
app.include_router(ecourts.router)
app.include_router(system.router)

