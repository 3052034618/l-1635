from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models import (
    Schedule, ScheduleStatus, Reservation, ReservationStatus,
    SeatLock, Route, RouteStation, Station, Employee, Vehicle
)
from app.schemas import (
    SmartReservationRequest, AlternativeSchedule, ReservationConflictResponse,
    ScheduleWithStats
)
from app.config import settings
from app.services.notification import notification_service


class ReservationService:
    def __init__(self, db: Session):
        self.db = db

    def _get_reserved_count(self, schedule_id: int) -> int:
        return self.db.query(Reservation).filter(
            Reservation.schedule_id == schedule_id,
            Reservation.status.in_([ReservationStatus.CONFIRMED, ReservationStatus.LOCKED])
        ).count()

    def _get_active_lock_count(self, schedule_id: int) -> int:
        now = datetime.utcnow()
        return self.db.query(SeatLock).filter(
            SeatLock.schedule_id == schedule_id,
            SeatLock.is_active == True,
            SeatLock.expires_at > now
        ).count()

    def get_available_seats(self, schedule_id: int) -> int:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule or not schedule.vehicle:
            return 0
        total_capacity = schedule.vehicle.capacity
        reserved = self._get_reserved_count(schedule_id)
        active_locks = self._get_active_lock_count(schedule_id)
        return max(0, total_capacity - reserved - active_locks)

    def _schedule_has_station(self, schedule_id: int, station_id: int) -> bool:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule:
            return False
        return self.db.query(RouteStation).filter(
            RouteStation.route_id == schedule.route_id,
            RouteStation.station_id == station_id
        ).first() is not None

    def check_time_conflict(
        self, employee_id: int, target_time: datetime, exclude_schedule_id: Optional[int] = None
    ) -> Optional[Reservation]:
        time_window_start = target_time - timedelta(minutes=60)
        time_window_end = target_time + timedelta(minutes=60)

        query = self.db.query(Reservation).join(Schedule).filter(
            Reservation.employee_id == employee_id,
            Reservation.status.in_([ReservationStatus.CONFIRMED, ReservationStatus.LOCKED]),
            Schedule.departure_time >= time_window_start,
            Schedule.departure_time <= time_window_end
        )
        if exclude_schedule_id:
            query = query.filter(Reservation.schedule_id != exclude_schedule_id)

        return query.first()

    def find_best_schedule(
        self, station_id: int, target_date: datetime.date,
        preferred_time: Optional[datetime] = None, direction: Optional[str] = None
    ) -> List[ScheduleWithStats]:
        start_of_day = datetime.combine(target_date, datetime.min.time())
        end_of_day = datetime.combine(target_date, datetime.max.time())

        query = self.db.query(Schedule).join(Route).filter(
            Schedule.departure_time >= start_of_day,
            Schedule.departure_time <= end_of_day,
            Schedule.status.in_([ScheduleStatus.PENDING, ScheduleStatus.ACTIVE]),
            Route.is_active == True
        )

        if direction:
            query = query.filter(Route.direction == direction)

        schedules = query.all()

        valid_schedules = []
        for sched in schedules:
            if not self._schedule_has_station(sched.id, station_id):
                continue
            available = self.get_available_seats(sched.id)
            if available <= 0:
                continue

            reserved = self._get_reserved_count(sched.id)
            sched_stats = ScheduleWithStats(
                **{c.name: getattr(sched, c.name) for c in sched.__table__.columns},
                reserved_count=reserved,
                available_seats=available
            )
            valid_schedules.append(sched_stats)

        def score_schedule(s: ScheduleWithStats) -> float:
            score = 0.0
            if preferred_time:
                time_diff = abs((s.departure_time - preferred_time).total_seconds() / 60)
                score -= time_diff * 0.5
            score += s.available_seats * 0.3
            load_ratio = s.reserved_count / (s.reserved_count + s.available_seats)
            score += (1 - load_ratio) * 10
            return score

        valid_schedules.sort(key=score_schedule, reverse=True)
        return valid_schedules

    def find_alternatives(
        self, employee_id: int, station_id: int, conflict_schedule: Schedule
    ) -> List[AlternativeSchedule]:
        target_date = conflict_schedule.departure_date
        alternatives = self.find_best_schedule(station_id, target_date)
        alternatives = [a for a in alternatives if a.id != conflict_schedule.id]

        result = []
        for alt in alternatives[:5]:
            time_diff = abs((alt.departure_time - conflict_schedule.departure_time).total_seconds() / 60)
            reason = f"时间差 {int(time_diff)} 分钟，剩 {alt.available_seats} 座"
            result.append(AlternativeSchedule(schedule=alt, score=time_diff, reason=reason))
        return result

    def lock_seat(self, schedule_id: int, employee_id: int, station_id: int) -> SeatLock:
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=settings.SEAT_LOCK_TIMEOUT)

        self.db.query(SeatLock).filter(
            SeatLock.schedule_id == schedule_id,
            SeatLock.employee_id == employee_id,
            SeatLock.is_active == True
        ).update({"is_active": False})

        lock = SeatLock(
            schedule_id=schedule_id,
            employee_id=employee_id,
            station_id=station_id,
            locked_at=now,
            expires_at=expires_at,
            is_active=True
        )
        self.db.add(lock)
        self.db.commit()
        self.db.refresh(lock)
        return lock

    def smart_reserve(self, request: SmartReservationRequest) -> dict:
        best_schedules = self.find_best_schedule(
            station_id=request.station_id,
            target_date=request.target_date,
            preferred_time=request.preferred_time,
            direction=request.direction
        )

        if not best_schedules:
            return {
                "success": False,
                "message": "当前日期无可预约班次",
                "alternatives": []
            }

        selected = None
        conflict = None
        alternatives = []

        for sched in best_schedules:
            conflict_res = self.check_time_conflict(request.employee_id, sched.departure_time)
            if not conflict_res:
                selected = sched
                break
            if not conflict:
                conflict = conflict_res
                alternatives = self.find_alternatives(
                    request.employee_id, request.station_id, conflict_res.schedule
                )

        if not selected:
            return {
                "success": False,
                "message": "存在预约冲突",
                "has_conflict": True,
                "conflict_reservation": conflict,
                "alternatives": alternatives
            }

        lock = self.lock_seat(selected.id, request.employee_id, request.station_id)

        reservation = Reservation(
            schedule_id=selected.id,
            employee_id=request.employee_id,
            station_id=request.station_id,
            reservation_date=request.target_date,
            status=ReservationStatus.CONFIRMED
        )
        self.db.add(reservation)
        self.db.commit()
        self.db.refresh(reservation)

        notification_service.create_notification(
            self.db,
            type="reservation_confirmed",
            employee_id=request.employee_id,
            title="预约成功",
            content=f"您已成功预约 {selected.departure_time.strftime('%Y-%m-%d %H:%M')} 的班车，线路：{selected.route.name if selected.route else '未知'}",
            related_id=reservation.id,
            related_type="reservation"
        )

        return {
            "success": True,
            "reservation": reservation,
            "schedule": selected,
            "seat_lock": lock
        }

    def cancel_reservation(self, reservation_id: int, employee_id: int) -> bool:
        reservation = self.db.query(Reservation).filter(
            Reservation.id == reservation_id,
            Reservation.employee_id == employee_id
        ).first()
        if not reservation:
            return False

        reservation.status = ReservationStatus.CANCELLED
        self.db.commit()

        notification_service.create_notification(
            self.db,
            type="reservation_cancelled",
            employee_id=employee_id,
            title="预约已取消",
            content=f"您的班车预约已取消",
            related_id=reservation_id,
            related_type="reservation"
        )
        return True

    def cleanup_expired_locks(self) -> int:
        now = datetime.utcnow()
        expired = self.db.query(SeatLock).filter(
            SeatLock.is_active == True,
            SeatLock.expires_at <= now
        ).all()
        count = len(expired)
        for lock in expired:
            lock.is_active = False
        self.db.commit()
        return count


def get_reservation_service(db: Session) -> ReservationService:
    return ReservationService(db)
