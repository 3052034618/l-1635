from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import logging
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

logger = logging.getLogger(__name__)


class ReservationService:
    def __init__(self, db: Session):
        self.db = db

    def _get_confirmed_reservation_count(self, schedule_id: int) -> int:
        return self.db.query(Reservation).filter(
            Reservation.schedule_id == schedule_id,
            Reservation.status.in_([ReservationStatus.CONFIRMED])
        ).count()

    def _get_active_lock_count(self, schedule_id: int, exclude_employee_id: Optional[int] = None) -> int:
        now = datetime.utcnow()
        query = self.db.query(SeatLock).filter(
            SeatLock.schedule_id == schedule_id,
            SeatLock.is_active == True,
            SeatLock.expires_at > now
        )
        if exclude_employee_id:
            query = query.filter(SeatLock.employee_id != exclude_employee_id)
        return query.count()

    def get_available_seats(self, schedule_id: int, exclude_employee_id: Optional[int] = None) -> int:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule or not schedule.vehicle:
            return 0
        total_capacity = schedule.vehicle.capacity
        confirmed = self._get_confirmed_reservation_count(schedule_id)
        active_locks = self._get_active_lock_count(schedule_id, exclude_employee_id)
        occupied = confirmed + active_locks
        logger.debug(
            f"班次[{schedule_id}] 座位统计: 容量={total_capacity}, "
            f"已确认={confirmed}, 活动锁={active_locks}, 占用={occupied}, 可用={max(0, total_capacity - occupied)}"
        )
        return max(0, total_capacity - occupied)

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
            Reservation.status.in_([ReservationStatus.CONFIRMED]),
            Schedule.departure_time >= time_window_start,
            Schedule.departure_time <= time_window_end
        )
        if exclude_schedule_id:
            query = query.filter(Reservation.schedule_id != exclude_schedule_id)

        return query.first()

    def find_best_schedule(
        self, station_id: int, target_date: datetime.date,
        preferred_time: Optional[datetime] = None, direction: Optional[str] = None,
        exclude_employee_id: Optional[int] = None
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
            available = self.get_available_seats(sched.id, exclude_employee_id)
            if available <= 0:
                continue

            reserved = self._get_confirmed_reservation_count(sched.id)
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
            total = s.reserved_count + s.available_seats
            load_ratio = s.reserved_count / total if total > 0 else 0
            score += (1 - load_ratio) * 10
            return score

        valid_schedules.sort(key=score_schedule, reverse=True)
        return valid_schedules

    def find_alternatives(
        self, employee_id: int, station_id: int, conflict_schedule: Schedule
    ) -> List[AlternativeSchedule]:
        target_date = conflict_schedule.departure_date
        alternatives = self.find_best_schedule(station_id, target_date, exclude_employee_id=employee_id)
        alternatives = [a for a in alternatives if a.id != conflict_schedule.id]

        result = []
        for alt in alternatives[:5]:
            time_diff = abs((alt.departure_time - conflict_schedule.departure_time).total_seconds() / 60)
            reason = f"时间差 {int(time_diff)} 分钟，剩 {alt.available_seats} 座"
            result.append(AlternativeSchedule(schedule=alt, score=time_diff, reason=reason))
        return result

    def lock_seat(self, schedule_id: int, employee_id: int, station_id: int) -> Optional[SeatLock]:
        try:
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=settings.SEAT_LOCK_TIMEOUT)

            self.db.query(SeatLock).filter(
                SeatLock.schedule_id == schedule_id,
                SeatLock.employee_id == employee_id,
                SeatLock.is_active == True
            ).update({"is_active": False})
            self.db.flush()

            available = self.get_available_seats(schedule_id, exclude_employee_id=employee_id)
            if available <= 0:
                logger.warning(f"锁座失败: 班次[{schedule_id}] 已无可用座位")
                self.db.rollback()
                return None

            lock = SeatLock(
                schedule_id=schedule_id,
                employee_id=employee_id,
                station_id=station_id,
                locked_at=now,
                expires_at=expires_at,
                is_active=True
            )
            self.db.add(lock)
            self.db.flush()
            return lock
        except Exception as e:
            logger.error(f"锁座异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def smart_reserve(self, request: SmartReservationRequest) -> dict:
        try:
            best_schedules = self.find_best_schedule(
                station_id=request.station_id,
                target_date=request.target_date,
                preferred_time=request.preferred_time,
                direction=request.direction,
                exclude_employee_id=request.employee_id
            )

            if not best_schedules:
                return {
                    "success": False,
                    "message": "当前日期无可预约班次",
                    "alternatives": []
                }

            selected = None
            selected_lock = None
            conflict = None
            alternatives = []

            for sched in best_schedules:
                conflict_res = self.check_time_conflict(request.employee_id, sched.departure_time)
                if conflict_res:
                    if not conflict:
                        conflict = conflict_res
                        alternatives = self.find_alternatives(
                            request.employee_id, request.station_id, conflict_res.schedule
                        )
                    continue

                lock = self.lock_seat(sched.id, request.employee_id, request.station_id)
                if lock:
                    selected = sched
                    selected_lock = lock
                    break

            if not selected or not selected_lock:
                if conflict:
                    return {
                        "success": False,
                        "message": "存在预约冲突",
                        "has_conflict": True,
                        "conflict_reservation": conflict,
                        "alternatives": alternatives
                    }
                return {
                    "success": False,
                    "message": "所有班次座位已满",
                    "alternatives": alternatives
                }

            existing_confirmed = self.db.query(Reservation).filter(
                Reservation.schedule_id == selected.id,
                Reservation.employee_id == request.employee_id,
                Reservation.status.in_([ReservationStatus.CONFIRMED])
            ).first()
            if existing_confirmed:
                selected_lock.is_active = False
                self.db.commit()
                return {
                    "success": False,
                    "message": "您已预约该班次",
                    "reservation": existing_confirmed,
                    "schedule": selected
                }

            old_cancelled = self.db.query(Reservation).filter(
                Reservation.schedule_id == selected.id,
                Reservation.employee_id == request.employee_id,
                Reservation.status == ReservationStatus.CANCELLED
            ).all()
            old_cancelled_ids = [r.id for r in old_cancelled]
            is_rebooking = len(old_cancelled) > 0

            if is_rebooking:
                for r in old_cancelled:
                    r.status = ReservationStatus.CANCELLED
                self.db.flush()
                logger.info(f"员工[{request.employee_id}]重新预约班次[{selected.id}]，旧记录{len(old_cancelled)}条")

            reservation = Reservation(
                schedule_id=selected.id,
                employee_id=request.employee_id,
                station_id=request.station_id,
                reservation_date=request.target_date,
                status=ReservationStatus.CONFIRMED
            )
            self.db.add(reservation)
            self.db.flush()

            selected_lock.is_active = False

            self.db.commit()
            self.db.refresh(reservation)
            self.db.refresh(selected_lock)

            try:
                route_name = selected.route.name if selected.route else "未知"
                notif_title = "预约成功（重新预约）" if is_rebooking else "预约成功"
                notif_content = (
                    f"您已重新预约 {selected.departure_time.strftime('%Y-%m-%d %H:%M')} 的班车，线路：{route_name}"
                    if is_rebooking
                    else f"您已成功预约 {selected.departure_time.strftime('%Y-%m-%d %H:%M')} 的班车，线路：{route_name}"
                )

                if is_rebooking:
                    from app.models import Notification
                    existing_same_notifs = self.db.query(Notification).filter(
                        Notification.employee_id == request.employee_id,
                        Notification.type == "reservation_confirmed",
                        Notification.related_type == "reservation",
                        Notification.related_id != None
                    ).all()
                    same_schedule_notifs = [
                        n for n in existing_same_notifs
                        if n.related_id in old_cancelled_ids or n.content.find(selected.departure_time.strftime('%Y-%m-%d %H:%M')) >= 0
                    ]
                    if same_schedule_notifs:
                        for n in same_schedule_notifs:
                            self.db.delete(n)
                        self.db.flush()
                        logger.info(f"员工[{request.employee_id}]重新预约，清理旧通知{len(same_schedule_notifs)}条")

                notification_service.create_notification(
                    self.db,
                    type="reservation_confirmed",
                    employee_id=request.employee_id,
                    title=notif_title,
                    content=notif_content,
                    related_id=reservation.id,
                    related_type="reservation",
                    extra={
                        "schedule_id": selected.id,
                        "departure_time": selected.departure_time.isoformat(),
                        "route_name": route_name,
                        "is_rebooking": is_rebooking,
                        "available_seats_after": self.get_available_seats(selected.id)
                    }
                )
            except Exception as e:
                logger.warning(f"发送预约成功通知失败（不影响预约结果）: {e}")

            available_after = self.get_available_seats(selected.id)
            return {
                "success": True,
                "reservation": reservation,
                "schedule": selected,
                "seat_lock": selected_lock,
                "available_seats_after": available_after
            }

        except Exception as e:
            logger.error(f"智能预约异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return {
                "success": False,
                "message": f"预约失败: {str(e)}",
                "alternatives": []
            }

    def cancel_reservation(self, reservation_id: int, employee_id: int) -> bool:
        try:
            reservation = self.db.query(Reservation).filter(
                Reservation.id == reservation_id,
                Reservation.employee_id == employee_id
            ).first()
            if not reservation:
                return False

            if reservation.status == ReservationStatus.CANCELLED:
                logger.info(f"预约[{reservation_id}]已为取消状态，无需重复取消")
                return True

            schedule_id = reservation.schedule_id
            reservation.status = ReservationStatus.CANCELLED
            self.db.flush()

            from app.models import Notification
            old_cancel_notifs = self.db.query(Notification).filter(
                Notification.employee_id == employee_id,
                Notification.type == "reservation_cancelled",
                Notification.related_type == "reservation"
            ).all()
            same_schedule_cancel = [
                n for n in old_cancel_notifs
                if (n.related_id is not None and n.related_id == reservation_id)
            ]
            if same_schedule_cancel:
                for n in same_schedule_cancel:
                    self.db.delete(n)
                self.db.flush()
                logger.info(f"清理重复取消通知{len(same_schedule_cancel)}条")

            self.db.commit()

            try:
                schedule = reservation.schedule
                dep_time = schedule.departure_time.strftime('%Y-%m-%d %H:%M') if schedule else ""
                notification_service.create_notification(
                    self.db,
                    type="reservation_cancelled",
                    employee_id=employee_id,
                    title="预约已取消",
                    content=f"您预约的 {dep_time} 班车已取消，座位已释放",
                    related_id=reservation_id,
                    related_type="reservation",
                    extra={
                        "schedule_id": schedule_id,
                        "departure_time": dep_time,
                        "released_seat": True
                    }
                )
            except Exception as e:
                logger.warning(f"发送取消预约通知失败（不影响取消结果）: {e}")

            return True
        except Exception as e:
            logger.error(f"取消预约异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return False

    def cleanup_expired_locks(self) -> int:
        try:
            now = datetime.utcnow()
            expired = self.db.query(SeatLock).filter(
                SeatLock.is_active == True,
                SeatLock.expires_at <= now
            ).all()
            count = len(expired)
            for lock in expired:
                lock.is_active = False
            self.db.commit()
            if count > 0:
                logger.info(f"清理过期座位锁定: {count} 个")
            return count
        except Exception as e:
            logger.error(f"清理座位锁定失败: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return 0


def get_reservation_service(db: Session) -> ReservationService:
    return ReservationService(db)
