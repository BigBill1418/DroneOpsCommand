import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, async_session, engine
from app.routers import auth, customers, aircraft, missions, flights, maps, reports, invoices, llm


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed data
    from app.seed import seed_database
    async with async_session() as session:
        await seed_database(session)

    # Ensure upload/report directories exist
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.reports_dir, exist_ok=True)

    yield

    await engine.dispose()


app = FastAPI(
    title="DroneOpsReport",
    description="Invoicing and after-action reporting tool for drone operations",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - allow all origins for development, restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for aircraft images
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount upload directory for serving images
if os.path.exists(settings.upload_dir):
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

# Register routers
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(aircraft.router)
app.include_router(missions.router)
app.include_router(flights.router)
app.include_router(maps.router)
app.include_router(reports.router)
app.include_router(invoices.router)
app.include_router(llm.router)


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "DroneOpsReport"}
