from datetime import date
from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DriverTask as DriverTaskSchema, DriverTaskComplete, DriverTaskCreate
from app.services.driver_task import DriverTaskService, get_driver_task_service

router = APIRouter(prefix="/driver-tasks", tags=["司机任务"])


@router.post("/assign-daily", summary="为司机分配当日任务")
def assign_daily_tasks(
    driver_id: int,
    task_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    service = get_driver_task_service(db)
    tasks = service.assign_daily_tasks(driver_id, task_date)
    return {"assigned": len(tasks), "tasks": tasks}


@router.post("/auto-assign-all", summary="为所有司机分配当日任务")
def auto_assign_all(task_date: Optional[date] = None, db: Session = Depends(get_db)):
    service = get_driver_task_service(db)
    return service.auto_assign_all_drivers(task_date)


@router.post("/{task_id}/start", summary="开始任务")
def start_task(task_id: int, start_mileage: float, db: Session = Depends(get_db)):
    service = get_driver_task_service(db)
    task = service.start_task(task_id, start_mileage)
    if not task:
        raise HTTPException(status_code=400, detail="无法开始任务")
    return {"success": True, "task": task}


@router.post("/{task_id}/complete", summary="完成任务")
def complete_task(task_id: int, data: DriverTaskComplete, db: Session = Depends(get_db)):
    service = get_driver_task_service(db)
    task = service.complete_task(task_id, data)
    if not task:
        raise HTTPException(status_code=400, detail="无法完成任务")
    return {"success": True, "task": task}


@router.get("/driver/{driver_id}", response_model=List[DriverTaskSchema], summary="获取司机任务列表")
def get_driver_tasks(
    driver_id: int,
    task_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    service = get_driver_task_service(db)
    return service.get_driver_tasks(driver_id, task_date)


@router.get("/by-date", response_model=List[DriverTaskSchema], summary="按日期获取任务")
def get_tasks_by_date(task_date: date, db: Session = Depends(get_db)):
    service = get_driver_task_service(db)
    return service.get_tasks_by_date(task_date)


@router.get("/driver/{driver_id}/stats", summary="获取司机任务统计")
def get_driver_task_stats(
    driver_id: int,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db)
):
    service = get_driver_task_service(db)
    return service.get_task_stats(driver_id, start_date, end_date)
