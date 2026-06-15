from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging

from app.config import settings
from app.database import engine, Base
from app.routers import (
    reservations, schedules, vehicle_locations,
    driver_tasks, performance, base_data, notifications
)
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.notification import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    loop = asyncio.get_running_loop()
    notification_service.set_event_loop(loop)
    logger.info("已设置通知服务的Event Loop引用，WebSocket实时推送已就绪")
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    description="企业班车智能调度系统后端API",
    version="1.1.0",
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
        "version": "1.1.0",
        "api_prefix": prefix,
        "docs": "/docs",
        "features": [
            "智能预约(冲突检测+替代推荐)",
            "按站点人数阈值自动取消班次",
            "座位锁定+余座精确统计",
            "车辆位置上报+ETA计算",
            "司机任务自动分配+里程油耗",
            "月度绩效报表+Excel导出",
            "WebSocket实时推送(预约/位置/取消)"
        ]
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
