from datetime import datetime, date
from typing import List, Optional
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Schedule, ScheduleStatus, Route, Vehicle, Driver
from app.schemas import ScheduleCreate, Schedule as ScheduleSchema, ScheduleWithStats
from app.services.schedule_management import ScheduleManagementService, get_schedule_management_service
from app.services.reservation import get_reservation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schedules", tags=["班次管理"])


@router.post("", response_model=ScheduleSchema, summary="创建班次")
def create_schedule(data: ScheduleCreate, db: Session = Depends(get_db)):
    try:
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
    except Exception as e:
        logger.error(f"创建班次失败: {e}", exc_info=True)
        try:
            db.rollback()
        except:
            pass
        raise HTTPException(status_code=500, detail=f"创建班次失败: {str(e)}")


@router.get("", response_model=List[ScheduleWithStats], summary="获取班次列表")
def list_schedules(
    route_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    try:
        query = db.query(Schedule)
        if route_id:
            query = query.filter(Schedule.route_id == route_id)
        if start_date:
            query = query.filter(Schedule.departure_date >= start_date)
        if end_date:
            query = query.filter(Schedule.departure_date <= end_date)
        if status:
            try:
                status_enum = ScheduleStatus(status)
                query = query.filter(Schedule.status == status_enum)
            except ValueError:
                logger.warning(f"无效的status筛选值: {status}，跳过该条件")
                pass

        schedules = query.order_by(Schedule.departure_time).all()
        reservation_service = get_reservation_service(db)

        result = []
        for s in schedules:
            try:
                reserved = reservation_service._get_confirmed_reservation_count(s.id)
                available = reservation_service.get_available_seats(s.id)
                capacity = s.vehicle.capacity if s.vehicle else 0
                if reserved + available > capacity > 0:
                    logger.warning(f"班次[{s.id}]座位统计异常: reserved={reserved}, available={available}, capacity={capacity}")
                    available = max(0, capacity - reserved)

                result.append(ScheduleWithStats(
                    **{c.name: getattr(s, c.name) for c in s.__table__.columns},
                    reserved_count=reserved,
                    available_seats=available
                ))
            except Exception as e:
                logger.warning(f"统计班次[{s.id}]座位失败，跳过: {e}")
                result.append(ScheduleWithStats(
                    **{c.name: getattr(s, c.name) for c in s.__table__.columns},
                    reserved_count=0,
                    available_seats=0
                ))
                continue

        return result
    except Exception as e:
        logger.error(f"查询班次列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询班次失败: {str(e)}")


@router.get("/{schedule_id}", response_model=ScheduleSchema, summary="获取班次详情")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    try:
        schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="班次不存在")
        return schedule
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取班次详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取班次失败: {str(e)}")


@router.post("/{schedule_id}/start", summary="发车")
def start_schedule(schedule_id: int, db: Session = Depends(get_db)):
    try:
        service = get_schedule_management_service(db)
        schedule = service.start_schedule(schedule_id)
        if not schedule:
            raise HTTPException(status_code=400, detail="无法启动班次")
        return {"success": True, "schedule": schedule}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"发车间次失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"发车失败: {str(e)}")


@router.post("/{schedule_id}/complete", summary="完成班次")
def complete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    try:
        service = get_schedule_management_service(db)
        schedule = service.complete_schedule(schedule_id)
        if not schedule:
            raise HTTPException(status_code=400, detail="无法完成班次")
        return {"success": True, "schedule": schedule}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"完成班次失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"完成班次失败: {str(e)}")


@router.post("/{schedule_id}/check-demand", summary="检查班次需求并自动取消")
def check_schedule_demand(schedule_id: int, db: Session = Depends(get_db)):
    try:
        service = get_schedule_management_service(db)
        return service.check_and_cancel_low_demand_schedule(schedule_id)
    except Exception as e:
        logger.error(f"检查班次需求失败: {e}", exc_info=True)
        return {
            "success": False,
            "schedule_id": schedule_id,
            "message": f"检查失败: {str(e)}",
            "cancelled": False
        }


@router.post("/check-upcoming", summary="检查所有即将发车的班次")
def check_all_upcoming(db: Session = Depends(get_db), minutes_before: int = 30):
    try:
        service = get_schedule_management_service(db)
        results = service.check_upcoming_schedules(minutes_before)
        return {"checked": len(results), "results": results}
    except Exception as e:
        logger.error(f"批量检查班次失败: {e}", exc_info=True)
        return {"checked": 0, "results": [], "error": str(e)}
