from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.database import engine, Base
from app.routers import (
    reservations, schedules, vehicle_locations,
    driver_tasks, performance, base_data, notifications
)
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    description="企业班车智能调度系统后端API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

prefix = settings.API_V1_PREFIX

app.include_router(base_data.router, prefix=prefix)
app.include_router(reservations.router, prefix=prefix)
app.include_router(schedules.router, prefix=prefix)
app.include_router(vehicle_locations.router, prefix=prefix)
app.include_router(driver_tasks.router, prefix=prefix)
app.include_router(performance.router, prefix=prefix)
app.include_router(notifications.router, prefix=prefix)


@app.get("/")
def root():
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "api_prefix": prefix,
        "docs": "/docs"
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
