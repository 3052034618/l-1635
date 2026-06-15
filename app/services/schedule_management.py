from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.models import (
    Schedule, ScheduleStatus, Reservation, ReservationStatus,
    RouteStation, Station, Employee
)
from app.config import settings
from app.services.notification import notification_service

logger = logging.getLogger(__name__)


class ScheduleManagementService:
    def __init__(self, db: Session):
        self.db = db

    def _get_route_stations(self, schedule_id: int) -> List[RouteStation]:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule:
            return []
        return self.db.query(RouteStation).filter(
            RouteStation.route_id == schedule.route_id
        ).order_by(RouteStation.sequence).all()

    def get_station_passenger_counts(self, schedule_id: int) -> List[Dict]:
        try:
            route_stations = self._get_route_stations(schedule_id)
            all_station_ids = [rs.station_id for rs in route_stations]

            reservations = self.db.query(Reservation).filter(
                Reservation.schedule_id == schedule_id,
                Reservation.status.in_([ReservationStatus.CONFIRMED])
            ).all()

            station_counts = {}
            for sid in all_station_ids:
                station = self.db.query(Station).filter(Station.id == sid).first()
                station_counts[sid] = {
                    "station_id": sid,
                    "station_name": station.name if station else "未知站点",
                    "count": 0,
                    "employees": [],
                    "is_served": True
                }

            for res in reservations:
                sid = res.station_id
                if sid not in station_counts:
                    station = res.station
                    station_counts[sid] = {
                        "station_id": sid,
                        "station_name": station.name if station else "未知站点",
                        "count": 0,
                        "employees": [],
                        "is_served": False
                    }
                station_counts[sid]["count"] += 1
                station_counts[sid]["employees"].append({
                    "employee_id": res.employee_id,
                    "employee_name": res.employee.name if res.employee else "未知员工"
                })

            return list(station_counts.values())
        except Exception as e:
            logger.error(f"获取站点人数统计失败: {e}", exc_info=True)
            return []

    def check_and_cancel_low_demand_schedule(self, schedule_id: int) -> Dict:
        try:
            schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
            if not schedule:
                return {"success": False, "message": "班次不存在"}

            if schedule.status in [ScheduleStatus.CANCELLED, ScheduleStatus.COMPLETED, ScheduleStatus.IN_PROGRESS]:
                return {"success": False, "message": f"班次状态为 {schedule.status}，无法取消"}

            station_counts = self.get_station_passenger_counts(schedule_id)
            total_passengers = sum(sc["count"] for sc in station_counts)
            threshold = schedule.min_passengers_threshold or settings.MIN_PASSENGERS_THRESHOLD

            low_demand_stations = [
                sc for sc in station_counts
                if sc["is_served"] and sc["count"] < threshold
            ]
            served_stations = [sc for sc in station_counts if sc["is_served"]]
            any_station_low = len(low_demand_stations) > 0 and len(served_stations) > 0

            result = {
                "schedule_id": schedule_id,
                "total_passengers": total_passengers,
                "threshold": threshold,
                "station_breakdown": station_counts,
                "low_demand_stations": [
                    {"station_id": s["station_id"], "station_name": s["station_name"], "count": s["count"]}
                    for s in low_demand_stations
                ],
                "cancelled": False
            }

            if any_station_low:
                low_names = ", ".join(s["station_name"] for s in low_demand_stations)
                self._cancel_schedule(schedule, station_counts, low_demand_stations)
                result["cancelled"] = True
                result["message"] = (
                    f"班次因站点人数不足已取消。"
                    f"低于阈值的站点: {low_names} (阈值 {threshold} 人)"
                )
                result["cancel_reason"] = "station_below_threshold"
            elif total_passengers == 0:
                self._cancel_schedule(schedule, station_counts, [])
                result["cancelled"] = True
                result["message"] = "班次因无任何预约已取消"
                result["cancel_reason"] = "no_passengers"
            else:
                result["message"] = f"所有站点预约人数均达标，共 {total_passengers} 人，正常运行"

            return result
        except Exception as e:
            logger.error(f"检查班次需求异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return {
                "success": False,
                "schedule_id": schedule_id,
                "message": f"检查失败: {str(e)}",
                "cancelled": False
            }

    def _cancel_schedule(
        self, schedule: Schedule, station_counts: List[Dict], low_demand_stations: List[Dict]
    ):
        try:
            schedule.status = ScheduleStatus.CANCELLED
            self.db.flush()

            low_station_ids = set(s["station_id"] for s in low_demand_stations)

            notified_count = 0
            for sc in station_counts:
                is_low = sc["station_id"] in low_station_ids or len(low_demand_stations) == 0
                for emp in sc["employees"]:
                    try:
                        self.db.query(Reservation).filter(
                            Reservation.schedule_id == schedule.id,
                            Reservation.employee_id == emp["employee_id"]
                        ).update({"status": ReservationStatus.CANCELLED})
                        self.db.flush()

                        reason_text = ""
                        if is_low and sc["station_id"] in low_station_ids:
                            reason_text = f"您的上车站点 [{sc['station_name']}] 预约人数不足。"
                        elif len(low_demand_stations) > 0:
                            low_names = ", ".join(s["station_name"] for s in low_demand_stations)
                            reason_text = f"站点 [{low_names}] 预约人数不足。"
                        else:
                            reason_text = "无任何乘客预约。"

                        dep_time = schedule.departure_time.strftime('%Y-%m-%d %H:%M')
                        notification_service.create_notification(
                            self.db,
                            type="schedule_cancelled",
                            employee_id=emp["employee_id"],
                            title="班次取消通知",
                            content=(
                                f"您预约的 {dep_time} 班车已取消。{reason_text}"
                                f"请及时调整您的出行计划。"
                            ),
                            related_id=schedule.id,
                            related_type="schedule",
                            extra={
                                "schedule_id": schedule.id,
                                "departure_time": dep_time,
                                "cancelled_due_to_station": sc["station_name"] if is_low else None,
                                "low_demand_stations": [
                                    {"station_id": s["station_id"], "station_name": s["station_name"]}
                                    for s in low_demand_stations
                                ]
                            }
                        )
                        notified_count += 1
                    except Exception as e:
                        logger.warning(f"通知员工[{emp['employee_id']}]班次取消失败: {e}")

            try:
                if schedule.driver_id:
                    dep_time = schedule.departure_time.strftime('%Y-%m-%d %H:%M')
                    notification_service.create_notification(
                        self.db,
                        type="schedule_cancelled",
                        driver_id=schedule.driver_id,
                        title="班次取消通知",
                        content=f"您有任务的班次 {dep_time} 已取消。",
                        related_id=schedule.id,
                        related_type="schedule",
                        extra={
                            "schedule_id": schedule.id,
                            "departure_time": dep_time
                        }
                    )
            except Exception as e:
                logger.warning(f"通知司机班次取消失败: {e}")

            self.db.commit()
            logger.info(
                f"班次[{schedule.id}]已取消，"
                f"影响员工 {sum(len(sc['employees']) for sc in station_counts)} 人，"
                f"成功发送通知 {notified_count} 条"
            )
        except Exception as e:
            logger.error(f"取消班次过程异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            raise

    def check_upcoming_schedules(self, minutes_before: int = None) -> List[Dict]:
        try:
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

            logger.info(f"发车前检查: 未来 {minutes_before} 分钟内有 {len(schedules)} 个班次待检查")

            results = []
            for sched in schedules:
                result = self.check_and_cancel_low_demand_schedule(sched.id)
                results.append(result)

            cancelled_count = sum(1 for r in results if r.get("cancelled"))
            if cancelled_count > 0:
                logger.info(f"发车前检查完成: 检查 {len(results)} 个班次，取消 {cancelled_count} 个")

            return results
        except Exception as e:
            logger.error(f"批量检查即将发车间次异常: {e}", exc_info=True)
            return []

    def activate_schedule(self, schedule_id: int) -> Optional[Schedule]:
        try:
            schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
            if schedule and schedule.status == ScheduleStatus.PENDING:
                schedule.status = ScheduleStatus.ACTIVE
                self.db.commit()
                self.db.refresh(schedule)
            return schedule
        except Exception as e:
            logger.error(f"激活班次异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def start_schedule(self, schedule_id: int) -> Optional[Schedule]:
        try:
            schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
            if schedule and schedule.status in [ScheduleStatus.PENDING, ScheduleStatus.ACTIVE]:
                schedule.status = ScheduleStatus.IN_PROGRESS
                schedule.actual_departure_time = datetime.utcnow()
                self.db.commit()
                self.db.refresh(schedule)
            return schedule
        except Exception as e:
            logger.error(f"发车间次异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def complete_schedule(self, schedule_id: int) -> Optional[Schedule]:
        try:
            schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
            if schedule and schedule.status == ScheduleStatus.IN_PROGRESS:
                schedule.status = ScheduleStatus.COMPLETED
                schedule.actual_arrival_time = datetime.utcnow()
                self.db.commit()
                self.db.refresh(schedule)
            return schedule
        except Exception as e:
            logger.error(f"完成班次异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def is_on_time(self, schedule: Schedule) -> bool:
        if not schedule.actual_departure_time:
            return True
        tolerance = timedelta(minutes=5)
        return abs((schedule.actual_departure_time - schedule.departure_time).total_seconds()) <= tolerance.total_seconds()


def get_schedule_management_service(db: Session) -> ScheduleManagementService:
    return ScheduleManagementService(db)
