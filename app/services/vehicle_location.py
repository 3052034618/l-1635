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
    def __init__(self, db: Session):
        self.db = db

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

        for res in reservations:
            try:
                station_eta = eta_by_station.get(res.station_id)
                if not station_eta:
                    continue

                if station_eta["status"] == "upcoming":
                    eta_min = station_eta["eta_minutes"]
                    station_name = station_eta["station_name"]
                    distance = station_eta["distance_km"]

                    should_notify = (
                        eta_min <= 30 or
                        (hasattr(res, '_last_notified_min') and abs(res._last_notified_min - eta_min) >= 5)
                    )
                    if not hasattr(res, '_last_notified_min') or should_notify:
                        notification_service.create_notification(
                            self.db,
                            type="vehicle_position_updated",
                            employee_id=res.employee_id,
                            title="班车位置更新",
                            content=f"班车距离 {station_name} 还有 {distance} 公里，预计 {eta_min} 分钟到达",
                            related_id=location.schedule_id,
                            related_type="schedule",
                            extra={
                                "schedule_id": location.schedule_id,
                                "station_id": res.station_id,
                                "station_name": station_name,
                                "eta_minutes": eta_min,
                                "distance_km": distance,
                                "position": {
                                    "latitude": location.latitude,
                                    "longitude": location.longitude,
                                    "speed": location.speed
                                }
                            }
                        )
            except Exception as e:
                logger.warning(f"通知员工[{res.employee_id}]位置更新失败: {e}")
                continue


def get_vehicle_location_service(db: Session) -> VehicleLocationService:
    return VehicleLocationService(db)
