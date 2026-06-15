from datetime import datetime, date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Schedule, ScheduleStatus, Route, Vehicle, Driver
from app.schemas import ScheduleCreate, Schedule as ScheduleSchema, ScheduleWithStats
from app.services.schedule_management import ScheduleManagementService, get_schedule_management_service
from app.services.reservation import get_reservation_service

router = APIRouter(prefix="/schedules", tags=["班次管理"])


@router.post("", response_model=ScheduleSchema, summary="创建班次")
def create_schedule(data: ScheduleCreate, db: Session = Depends(get_db)):
    schedule = Schedule(
        route_id=data.route_id,
        vehicle_id=data.vehicle_id,
        driver_id=data.driver_id,
        departure_time=data.departure_time,
        departure_date=data.departure_time.date(),
        arrival_time=data.arrival_time,
        status=ScheduleStatus.PENDING,
        min_passengers_threshold=data.min_passengers_threshold
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.get("", response_model=List[ScheduleWithStats], summary="获取班次列表")
def list_schedules(
    route_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Schedule)
    if route_id:
        query = query.filter(Schedule.route_id == route_id)
    if start_date:
        query = query.filter(Schedule.departure_date >= start_date)
    if end_date:
        query = query.filter(Schedule.departure_date <= end_date)
    if status:
        query = query.filter(Schedule.status == status)

    schedules = query.order_by(Schedule.departure_time).all()
    reservation_service = get_reservation_service(db)

    result = []
    for s in schedules:
        reserved = reservation_service._get_reserved_count(s.id)
        available = reservation_service.get_available_seats(s.id)
        result.append(ScheduleWithStats(
            **{c.name: getattr(s, c.name) for c in s.__table__.columns},
            reserved_count=reserved,
            available_seats=available
        ))
    return result


@router.get("/{schedule_id}", response_model=ScheduleSchema, summary="获取班次详情")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="班次不存在")
    return schedule


@router.post("/{schedule_id}/start", summary="发车")
def start_schedule(schedule_id: int, db: Session = Depends(get_db)):
    service = get_schedule_management_service(db)
    schedule = service.start_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=400, detail="无法启动班次")
    return {"success": True, "schedule": schedule}


@router.post("/{schedule_id}/complete", summary="完成班次")
def complete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    service = get_schedule_management_service(db)
    schedule = service.complete_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=400, detail="无法完成班次")
    return {"success": True, "schedule": schedule}


@router.post("/{schedule_id}/check-demand", summary="检查班次需求并自动取消")
def check_schedule_demand(schedule_id: int, db: Session = Depends(get_db)):
    service = get_schedule_management_service(db)
    return service.check_and_cancel_low_demand_schedule(schedule_id)


@router.post("/check-upcoming", summary="检查所有即将发车的班次")
def check_all_upcoming(db: Session = Depends(get_db), minutes_before: int = 30):
    service = get_schedule_management_service(db)
    results = service.check_upcoming_schedules(minutes_before)
    return {"checked": len(results), "results": results}
