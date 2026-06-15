from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from geopy.distance import geodesic

from app.models import (
    VehicleLocation, Schedule, RouteStation, Station,
    ScheduleStatus, Reservation
)
from app.schemas import VehicleLocationCreate
from app.services.notification import notification_service


class VehicleLocationService:
    def __init__(self, db: Session):
        self.db = db

    def report_location(self, data: VehicleLocationCreate) -> VehicleLocation:
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
            self._notify_passengers_location_update(location)

        return location

    def _get_route_stations(self, schedule_id: int) -> List[RouteStation]:
        schedule = self.db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not schedule:
            return []
        return self.db.query(RouteStation).filter(
            RouteStation.route_id == schedule.route_id
        ).order_by(RouteStation.sequence).all()

    def calculate_eta(self, schedule_id: int) -> Dict:
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
            station_pos = (station.latitude, station.longitude)

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
            "current_position": {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "speed": location.speed
            },
            "stations_eta": eta_results
        }

    def get_latest_location(self, vehicle_id: int) -> Optional[VehicleLocation]:
        return self.db.query(VehicleLocation).filter(
            VehicleLocation.vehicle_id == vehicle_id
        ).order_by(VehicleLocation.reported_at.desc()).first()

    def get_location_history(
        self, vehicle_id: int, start_time: datetime, end_time: datetime
    ) -> List[VehicleLocation]:
        return self.db.query(VehicleLocation).filter(
            VehicleLocation.vehicle_id == vehicle_id,
            VehicleLocation.reported_at >= start_time,
            VehicleLocation.reported_at <= end_time
        ).order_by(VehicleLocation.reported_at).all()

    def _notify_passengers_location_update(self, location: VehicleLocation):
        if not location.schedule_id:
            return

        reservations = self.db.query(Reservation).filter(
            Reservation.schedule_id == location.schedule_id,
            Reservation.status == "confirmed"
        ).all()

        eta_info = self.calculate_eta(location.schedule_id)

        for res in reservations:
            station_eta = next(
                (s for s in eta_info.get("stations_eta", []) if s["station_id"] == res.station_id),
                None
            )
            if station_eta and station_eta["status"] == "upcoming":
                notification_service.create_notification(
                    self.db,
                    type="vehicle_position_updated",
                    employee_id=res.employee_id,
                    title="班车位置更新",
                    content=f"班车预计 {station_eta['eta_minutes']} 分钟后到达 {station_eta['station_name']}",
                    related_id=location.schedule_id,
                    related_type="schedule"
                )


def get_vehicle_location_service(db: Session) -> VehicleLocationService:
    return VehicleLocationService(db)
