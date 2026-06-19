from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    auth,
    categories,
    dashboard,
    fee_structures,
    imports,
    installments,
    student_fees,
    students,
)
from app.seed import seed_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed_admin()
    yield


app = FastAPI(title="Plutus — School Fee Management API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(students.router)
app.include_router(fee_structures.router)
app.include_router(student_fees.router)
app.include_router(installments.router)
app.include_router(dashboard.router)
app.include_router(imports.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "academic_year": settings.default_academic_year}
