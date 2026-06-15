from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.models import (
    Schedule, ScheduleStatus, Reservation, ReservationStatus,
    RouteStation, Station, Employee
)
from app.config import settings
from app.services.notification import notification_service


class ScheduleManagementService:
    def __init__(self, db: Session):
        self.db = db

    def get_station_passenger_counts(self, schedule_id: int) -> List[Dict]:
        reservations = self.db.query(Reservation).filter(
            Reservation.schedule_id == schedule_id,
            Reservation.status.in_([ReservationStatus.CONFIRMED, ReservationStatus.LOCKED])
        ).all()

        station_counts = {}
        for res in reservations:
            if res.station_id not in station_counts:
                station_counts[res.station_id] = {
                    "station_id": res.station_id,
                    "station_name": res.station.name if res.station else "未知站点",
                    "count": 0,
                    "employees": []
                }
            station_counts[res.station_id]["count"] += 1
            station_counts[res.station_id]["employees"].append({
                "employee_id": res.employee_id,
                "employee_name": res.employee.name if res.employee else "未知员工"
            })

        return list(station_counts.values())

    def check_and_cancel_low_demand_schedule(self, schedule_id: int) -> Dict:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule:
            return {"success": False, "message": "班次不存在"}

        if schedule.status in [ScheduleStatus.CANCELLED, ScheduleStatus.COMPLETED, ScheduleStatus.IN_PROGRESS]:
            return {"success": False, "message": f"班次状态为 {schedule.status}，无法取消"}

        station_counts = self.get_station_passenger_counts(schedule_id)
        total_passengers = sum(sc["count"] for sc in station_counts)
        threshold = schedule.min_passengers_threshold or settings.MIN_PASSENGERS_THRESHOLD

        result = {
            "schedule_id": schedule_id,
            "total_passengers": total_passengers,
            "threshold": threshold,
            "station_breakdown": station_counts,
            "cancelled": False
        }

        if total_passengers < threshold:
            self._cancel_schedule(schedule, station_counts)
            result["cancelled"] = True
            result["message"] = f"班次因预约人数不足（{total_passengers}/{threshold}）已取消"
        else:
            result["message"] = f"班次预约人数充足（{total_passengers}/{threshold}），正常运行"

        return result

    def _cancel_schedule(self, schedule: Schedule, station_counts: List[Dict]):
        schedule.status = ScheduleStatus.CANCELLED
        self.db.commit()

        for sc in station_counts:
            for emp in sc["employees"]:
                self.db.query(Reservation).filter(
                    Reservation.schedule_id == schedule.id,
                    Reservation.employee_id == emp["employee_id"]
                ).update({"status": ReservationStatus.CANCELLED})

                notification_service.create_notification(
                    self.db,
                    type="schedule_cancelled",
                    employee_id=emp["employee_id"],
                    title="班次取消通知",
                    content=f"您预约的 {schedule.departure_time.strftime('%Y-%m-%d %H:%M')} 班车因人数不足已取消，请调整您的出行计划。",
                    related_id=schedule.id,
                    related_type="schedule"
                )

        if schedule.driver_id:
            notification_service.create_notification(
                self.db,
                type="schedule_cancelled",
                driver_id=schedule.driver_id,
                title="班次取消通知",
                content=f"您有任务的班次 {schedule.departure_time.strftime('%Y-%m-%d %H:%M')} 已取消。",
                related_id=schedule.id,
                related_type="schedule"
            )

        self.db.commit()

    def check_upcoming_schedules(self, minutes_before: int = None) -> List[Dict]:
        if minutes_before is None:
            minutes_before = settings.NOTIFY_BEFORE_DEPARTURE

        now = datetime.utcnow()
        check_window_start = now
        check_window_end = now + timedelta(minutes=minutes_before)

        schedules = self.db.query(Schedule).filter(
            Schedule.status.in_([ScheduleStatus.PENDING, ScheduleStatus.ACTIVE]),
            Schedule.departure_time >= check_window_start,
            Schedule.departure_time <= check_window_end
        ).all()

        results = []
        for sched in schedules:
            result = self.check_and_cancel_low_demand_schedule(sched.id)
            results.append(result)

        return results

    def activate_schedule(self, schedule_id: int) -> Optional[Schedule]:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if schedule and schedule.status == ScheduleStatus.PENDING:
            schedule.status = ScheduleStatus.ACTIVE
            self.db.commit()
            self.db.refresh(schedule)
        return schedule

    def start_schedule(self, schedule_id: int) -> Optional[Schedule]:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if schedule and schedule.status in [ScheduleStatus.PENDING, ScheduleStatus.ACTIVE]:
            schedule.status = ScheduleStatus.IN_PROGRESS
            schedule.actual_departure_time = datetime.utcnow()
            self.db.commit()
            self.db.refresh(schedule)
        return schedule

    def complete_schedule(self, schedule_id: int) -> Optional[Schedule]:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if schedule and schedule.status == ScheduleStatus.IN_PROGRESS:
            schedule.status = ScheduleStatus.COMPLETED
            schedule.actual_arrival_time = datetime.utcnow()
            self.db.commit()
            self.db.refresh(schedule)
        return schedule

    def is_on_time(self, schedule: Schedule) -> bool:
        if not schedule.actual_departure_time:
            return True
        tolerance = timedelta(minutes=5)
        return abs((schedule.actual_departure_time - schedule.departure_time).total_seconds()) <= tolerance.total_seconds()


def get_schedule_management_service(db: Session) -> ScheduleManagementService:
    return ScheduleManagementService(db)
