from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import VehicleLocationCreate, VehicleLocation as VehicleLocationSchema
from app.services.vehicle_location import VehicleLocationService, get_vehicle_location_service
from app.services.websocket import manager, notify_employee
from app.models import Reservation, ReservationStatus

router = APIRouter(prefix="/vehicle-locations", tags=["车辆位置"])


@router.post("/report", response_model=VehicleLocationSchema, summary="上报车辆位置")
async def report_location(
    data: VehicleLocationCreate,
    db: Session = Depends(get_db)
):
    service = get_vehicle_location_service(db)
    location = service.report_location(data)

    if data.schedule_id:
        reservations = db.query(Reservation).filter(
            Reservation.schedule_id == data.schedule_id,
            Reservation.status == ReservationStatus.CONFIRMED
        ).all()

        eta_info = service.calculate_eta(data.schedule_id)
        for res in reservations:
            station_eta = next(
                (s for s in eta_info.get("stations_eta", []) if s["station_id"] == res.station_id),
                None
            )
            if station_eta:
                await notify_employee(
                    res.employee_id,
                    "vehicle_position_updated",
                    "班车位置更新",
                    f"班车预计 {station_eta['eta_minutes']} 分钟后到达 {station_eta['station_name']}",
                    related_id=data.schedule_id,
                    related_type="schedule",
                    extra={"eta": station_eta, "position": {"lat": data.latitude, "lng": data.longitude}}
                )

    return location


@router.get("/{vehicle_id}/latest", summary="获取车辆最新位置")
def get_latest_location(vehicle_id: int, db: Session = Depends(get_db)):
    service = get_vehicle_location_service(db)
    location = service.get_latest_location(vehicle_id)
    if not location:
        raise HTTPException(status_code=404, detail="暂无位置数据")
    return location


@router.get("/schedule/{schedule_id}/eta", summary="获取班次ETA信息")
def get_schedule_eta(schedule_id: int, db: Session = Depends(get_db)):
    service = get_vehicle_location_service(db)
    return service.calculate_eta(schedule_id)


@router.get("/{vehicle_id}/history", summary="获取车辆位置历史")
def get_location_history(
    vehicle_id: int,
    start_time: datetime,
    end_time: datetime,
    db: Session = Depends(get_db)
):
    service = get_vehicle_location_service(db)
    return service.get_location_history(vehicle_id, start_time, end_time)


@router.websocket("/ws/employee/{employee_id}")
async def websocket_employee(websocket: WebSocket, employee_id: int):
    await manager.connect_employee(websocket, employee_id)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/ws/driver/{driver_id}")
async def websocket_driver(websocket: WebSocket, driver_id: int):
    await manager.connect_driver(websocket, driver_id)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
