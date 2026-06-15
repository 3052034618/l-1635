from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    SmartReservationRequest, Reservation, AlternativeSchedule,
    ReservationConflictResponse
)
from app.services.reservation import ReservationService, get_reservation_service

router = APIRouter(prefix="/reservations", tags=["预约管理"])


@router.post("/smart", response_model=dict, summary="智能预约班车")
def smart_reserve(
    request: SmartReservationRequest,
    db: Session = Depends(get_db)
):
    service = get_reservation_service(db)
    result = service.smart_reserve(request)
    return result


@router.get("/employee/{employee_id}", response_model=List[Reservation], summary="获取员工预约列表")
def get_employee_reservations(
    employee_id: int,
    target_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    from app.models import Reservation as ReservationModel
    query = db.query(ReservationModel).filter(ReservationModel.employee_id == employee_id)
    if target_date:
        query = query.filter(ReservationModel.reservation_date == target_date)
    return query.order_by(ReservationModel.created_at.desc()).all()


@router.get("/schedule/{schedule_id}", summary="获取班次预约统计")
def get_schedule_reservations(
    schedule_id: int,
    db: Session = Depends(get_db)
):
    service = get_reservation_service(db)
    from app.services.schedule_management import get_schedule_management_service
    mgmt_service = get_schedule_management_service(db)

    station_counts = mgmt_service.get_station_passenger_counts(schedule_id)
    available_seats = service.get_available_seats(schedule_id)

    return {
        "schedule_id": schedule_id,
        "available_seats": available_seats,
        "station_breakdown": station_counts
    }


@router.delete("/{reservation_id}", summary="取消预约")
def cancel_reservation(
    reservation_id: int,
    employee_id: int,
    db: Session = Depends(get_db)
):
    service = get_reservation_service(db)
    success = service.cancel_reservation(reservation_id, employee_id)
    if not success:
        raise HTTPException(status_code=404, detail="预约不存在或无法取消")
    return {"success": True, "message": "预约已取消"}


@router.get("/alternatives", response_model=List[AlternativeSchedule], summary="获取替代班次推荐")
def get_alternative_schedules(
    employee_id: int,
    station_id: int,
    target_date: date,
    db: Session = Depends(get_db)
):
    service = get_reservation_service(db)
    schedules = service.find_best_schedule(station_id, target_date)

    alternatives = []
    for sched in schedules[:5]:
        alternatives.append(AlternativeSchedule(
            schedule=sched,
            score=float(sched.available_seats),
            reason=f"剩余 {sched.available_seats} 座位，已预约 {sched.reserved_count} 人"
        ))
    return alternatives
