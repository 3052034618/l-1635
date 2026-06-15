from datetime import datetime
from typing import Optional, List, Dict
import logging
from sqlalchemy.orm import Session
from geopy.distance import geodesic

from app.models import (
    VehicleLocation, Schedule, RouteStation, Station,
    ScheduleStatus, Reservation, ReservationStatus
)
from app.schemas import VehicleLocationCreate
from app.services.notification import notification_service

logger = logging.getLogger(__name__)


class VehicleLocationService:
    _last_push_state = {}

    def __init__(self, db: Session):
        self.db = db

    def _should_push_location_update(
        self, employee_id: int, schedule_id: int, station_id: int,
        eta_min: float, now: datetime
    ) -> bool:
        key = f"{employee_id}_{schedule_id}_{station_id}"
        state = self._last_push_state.get(key)

        key_milestones = [30, 20, 15, 10, 5, 2, 1]
        crossed_milestone = False
        if state:
            for m in key_milestones:
                if state["last_eta"] > m >= eta_min:
                    crossed_milestone = True
                    break

        if not state:
            self._last_push_state[key] = {
                "last_push": now,
                "last_eta": eta_min,
                "push_count": 1
            }
            return eta_min <= 30

        time_since_last = (now - state["last_push"]).total_seconds()
        eta_change = abs(state["last_eta"] - eta_min)

        should_push = False
        reason = ""

        if crossed_milestone:
            should_push = True
            reason = f"跨越里程碑:{eta_min:.0f}分钟"
        elif eta_min <= 30 and time_since_last >= 180 and eta_change >= 2:
            should_push = True
            reason = f"ETA变化{eta_change:.1f}分钟，间隔{time_since_last:.0f}秒"
        elif eta_min <= 10 and time_since_last >= 120:
            should_push = True
            reason = f"临近到站，定时推送"
        elif time_since_last >= 600:
            should_push = True
            reason = "10分钟心跳推送"

        if should_push:
            self._last_push_state[key] = {
                "last_push": now,
                "last_eta": eta_min,
                "push_count": state["push_count"] + 1,
                "last_reason": reason
            }
            logger.debug(
                f"员工[{employee_id}]班次[{schedule_id}]站点[{station_id}] "
                f"推送触发: {reason}, ETA={eta_min:.1f}min, 累计推送{state['push_count'] + 1}次"
            )

        return should_push

    def _cleanup_old_push_state(self):
        now = datetime.utcnow()
        expired_keys = []
        for key, state in self._last_push_state.items():
            if (now - state["last_push"]).total_seconds() > 7200:
                expired_keys.append(key)
        for key in expired_keys:
            del self._last_push_state[key]
        if expired_keys:
            logger.debug(f"清理过期推送状态: {len(expired_keys)} 条")

    def report_location(self, data: VehicleLocationCreate) -> Optional[VehicleLocation]:
        try:
            location = VehicleLocation(
                vehicle_id=data.vehicle_id,
                schedule_id=data.schedule_id,
                latitude=data.latitude,
                longitude=data.longitude,
                speed=data.speed or 0,
                heading=data.heading,
                reported_at=datetime.utcnow()
            )
            self.db.add(location)
            self.db.commit()
            self.db.refresh(location)

            if data.schedule_id:
                try:
                    self._notify_passengers_location_update(location)
                except Exception as e:
                    logger.warning(f"通知乘客位置更新失败（不影响位置上报）: {e}")

            return location
        except Exception as e:
            logger.error(f"上报车辆位置异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def _get_route_stations(self, schedule_id: int) -> List[RouteStation]:
        try:
            schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
            if not schedule:
                return []
            return self.db.query(RouteStation).filter(
                RouteStation.route_id == schedule.route_id
            ).order_by(RouteStation.sequence).all()
        except Exception as e:
            logger.error(f"获取线路站点失败: {e}")
            return []

    def calculate_eta(self, schedule_id: int) -> Dict:
        try:
            location = self.db.query(VehicleLocation).filter(
                VehicleLocation.schedule_id == schedule_id
            ).order_by(VehicleLocation.reported_at.desc()).first()

            if not location:
                return {"error": "暂无位置数据"}

            route_stations = self._get_route_stations(schedule_id)
            if not route_stations:
                return {"error": "线路站点信息缺失"}

            current_pos = (location.latitude, location.longitude)
            current_speed = max(location.speed or 30, 10)

            eta_results = []
            passed_current = False

            for rs in route_stations:
                station = rs.station
                if not station:
                    continue
                station_pos = (station.latitude, station.longitude)
                if None in station_pos:
                    continue

                distance_km = geodesic(current_pos, station_pos).kilometers
                travel_minutes = (distance_km / current_speed) * 60 if current_speed > 0 else 999

                if not passed_current and distance_km < 0.5:
                    passed_current = True
                    eta_results.append({
                        "station_id": station.id,
                        "station_name": station.name,
                        "distance_km": round(distance_km, 2),
                        "eta_minutes": 0,
                        "status": "current"
                    })
                    continue

                if passed_current or distance_km >= 0.5:
                    passed_current = True
                    eta_results.append({
                        "station_id": station.id,
                        "station_name": station.name,
                        "distance_km": round(distance_km, 2),
                        "eta_minutes": round(travel_minutes, 1),
                        "status": "upcoming"
                    })

            return {
                "schedule_id": schedule_id,
                "current_position": {
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "speed": location.speed
                },
                "reported_at": location.reported_at.isoformat(),
                "stations_eta": eta_results
            }
        except Exception as e:
            logger.error(f"计算ETA异常: {e}", exc_info=True)
            return {"error": f"计算失败: {str(e)}"}

    def get_latest_location(self, vehicle_id: int) -> Optional[VehicleLocation]:
        try:
            return self.db.query(VehicleLocation).filter(
                VehicleLocation.vehicle_id == vehicle_id
            ).order_by(VehicleLocation.reported_at.desc()).first()
        except Exception as e:
            logger.error(f"获取最新位置失败: {e}")
            return None

    def get_location_history(
        self, vehicle_id: int, start_time: datetime, end_time: datetime
    ) -> List[VehicleLocation]:
        try:
            return self.db.query(VehicleLocation).filter(
                VehicleLocation.vehicle_id == vehicle_id,
                VehicleLocation.reported_at >= start_time,
                VehicleLocation.reported_at <= end_time
            ).order_by(VehicleLocation.reported_at).all()
        except Exception as e:
            logger.error(f"获取位置历史失败: {e}")
            return []

    def _notify_passengers_location_update(self, location: VehicleLocation):
        if not location.schedule_id:
            return

        try:
            reservations = self.db.query(Reservation).filter(
                Reservation.schedule_id == location.schedule_id,
                Reservation.status == ReservationStatus.CONFIRMED
            ).all()
        except Exception as e:
            logger.warning(f"查询预约列表失败，跳过通知: {e}")
            return

        if not reservations:
            return

        eta_info = self.calculate_eta(location.schedule_id)
        stations_eta = eta_info.get("stations_eta", [])
        eta_by_station = {s["station_id"]: s for s in stations_eta}
        now = datetime.utcnow()

        self._cleanup_old_push_state()

        from app.models import Notification

        for res in reservations:
            try:
                station_eta = eta_by_station.get(res.station_id)
                if not station_eta or station_eta["status"] != "upcoming":
                    continue

                eta_min = station_eta["eta_minutes"]
                station_name = station_eta["station_name"]
                distance = station_eta["distance_km"]

                if not self._should_push_location_update(
                    res.employee_id, location.schedule_id, res.station_id, eta_min, now
                ):
                    continue

                old_pos_notifs = self.db.query(Notification).filter(
                    Notification.employee_id == res.employee_id,
                    Notification.type == "vehicle_position_updated",
                    Notification.related_id == location.schedule_id,
                    Notification.related_type == "schedule"
                ).all()
                if len(old_pos_notifs) >= 3:
                    old_pos_notifs_sorted = sorted(
                        old_pos_notifs, key=lambda n: n.created_at
                    )
                    for n in old_pos_notifs_sorted[:-2]:
                        self.db.delete(n)
                    self.db.flush()

                if eta_min <= 1:
                    content = f"班车即将到达 {station_name}，请做好乘车准备"
                    title = "班车即将到站"
                elif eta_min <= 5:
                    content = f"班车距离 {station_name} 还有 {distance:.1f} 公里，预计 {eta_min:.0f} 分钟到达"
                    title = "班车即将到站"
                else:
                    content = f"班车距离 {station_name} 还有 {distance:.1f} 公里，预计 {eta_min:.0f} 分钟到达"
                    title = "班车位置更新"

                notification_service.create_notification(
                    self.db,
                    type="vehicle_position_updated",
                    employee_id=res.employee_id,
                    title=title,
                    content=content,
                    related_id=location.schedule_id,
                    related_type="schedule",
                    extra={
                        "schedule_id": location.schedule_id,
                        "station_id": res.station_id,
                        "station_name": station_name,
                        "eta_minutes": round(eta_min, 1),
                        "distance_km": round(distance, 2),
                        "position": {
                            "latitude": location.latitude,
                            "longitude": location.longitude,
                            "speed": location.speed
                        },
                        "push_time": now.isoformat()
                    }
                )

            except Exception as e:
                logger.warning(f"通知员工[{res.employee_id}]位置更新失败: {e}")
                continue


def get_vehicle_location_service(db: Session) -> VehicleLocationService:
    return VehicleLocationService(db)
