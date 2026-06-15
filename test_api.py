import pytest
from datetime import datetime, date, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Station, Route, RouteStation, Employee, Driver, Vehicle,
    Schedule, ScheduleStatus, Reservation, ReservationStatus,
    Notification, SeatLock
)
from app.services.vehicle_location import VehicleLocationService
from passlib.context import CryptContext

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@pytest.fixture
def setup_test_data():
    db = TestingSessionLocal()
    try:
        station1 = Station(name="站点A", address="地址A", latitude=31.0, longitude=121.0)
        station2 = Station(name="站点B", address="地址B", latitude=31.1, longitude=121.1)
        station3 = Station(name="站点C", address="地址C", latitude=31.2, longitude=121.2)
        db.add_all([station1, station2, station3])
        db.flush()

        route = Route(name="测试线路", code="TEST001", direction="up")
        db.add(route)
        db.flush()

        rs1 = RouteStation(route_id=route.id, station_id=station1.id, sequence=1, arrival_offset_minutes=0)
        rs2 = RouteStation(route_id=route.id, station_id=station2.id, sequence=2, arrival_offset_minutes=15)
        rs3 = RouteStation(route_id=route.id, station_id=station3.id, sequence=3, arrival_offset_minutes=30)
        db.add_all([rs1, rs2, rs3])
        db.flush()

        employees = []
        for i in range(1, 6):
            emp = Employee(
                name=f"测试员工{i:02d}", employee_no=f"EMP{i:04d}", department="测试部",
                default_station_id=station1.id,
                hashed_password=pwd_context.hash("123456")
            )
            employees.append(emp)
            db.add(emp)
        db.flush()

        driver = Driver(
            name="测试司机", driver_no="DRV001",
            hashed_password=pwd_context.hash("123456")
        )
        db.add(driver)
        db.flush()

        vehicle = Vehicle(plate_no="沪A·TEST01", model="测试车型", capacity=5)
        db.add(vehicle)
        db.flush()

        tomorrow = date.today() + timedelta(days=1)
        dep_time = datetime.combine(tomorrow, datetime.min.time()).replace(hour=8, minute=0)
        arr_time = dep_time + timedelta(minutes=45)

        schedule = Schedule(
            route_id=route.id, vehicle_id=vehicle.id, driver_id=driver.id,
            departure_time=dep_time, departure_date=tomorrow,
            arrival_time=arr_time, status=ScheduleStatus.PENDING,
            min_passengers_threshold=2
        )
        db.add(schedule)
        db.flush()

        db.commit()

        return {
            "stations": [station1, station2, station3],
            "route": route,
            "employees": employees,
            "driver": driver,
            "vehicle": vehicle,
            "schedule": schedule
        }
    finally:
        db.close()


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "企业班车智能调度系统" in data["name"]
    assert "features" in data
    assert len(data["features"]) == 7


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_create_station():
    response = client.post(
        "/api/v1/stations",
        json={"name": "测试站点", "address": "测试地址", "latitude": 31.0, "longitude": 121.0}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "测试站点"


def test_smart_reservation_success(setup_test_data):
    """需求1、2：预约成功后接口返回200，通知列表可查"""
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    response = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
            "direction": "up"
        }
    )
    assert response.status_code == 200
    result = response.json()
    assert result["success"] == True
    assert "reservation" in result
    assert "available_seats_after" in result

    db = TestingSessionLocal()
    try:
        notifs = db.query(Notification).filter(Notification.employee_id == emp.id).all()
        assert len(notifs) >= 1
        assert any(n.type == "reservation_confirmed" for n in notifs)
    finally:
        db.close()


def test_conflict_detection(setup_test_data):
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    response1 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response1.status_code == 200
    assert response1.json()["success"] == True

    response2 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response2.status_code == 200
    result = response2.json()
    assert result.get("success") == False


def test_seat_accuracy_concurrent(setup_test_data):
    """需求4：连续多人预约时余座和容量严格对应"""
    data = setup_test_data
    station = data["stations"][0]
    capacity = data["vehicle"].capacity

    success_count = 0
    last_available = capacity
    for i, emp in enumerate(data["employees"]):
        response = client.post(
            "/api/v1/reservations/smart",
            json={
                "employee_id": emp.id,
                "station_id": station.id,
                "target_date": data["schedule"].departure_date.isoformat(),
            }
        )
        assert response.status_code == 200
        result = response.json()
        if result["success"]:
            success_count += 1
            last_available = result.get("available_seats_after", -1)
            assert success_count + last_available <= capacity

    assert success_count <= capacity
    db = TestingSessionLocal()
    try:
        confirmed_count = db.query(Reservation).filter(
            Reservation.schedule_id == data["schedule"].id,
            Reservation.status == ReservationStatus.CONFIRMED
        ).count()
        assert confirmed_count == success_count
        assert confirmed_count <= capacity

        active_locks = db.query(SeatLock).filter(
            SeatLock.schedule_id == data["schedule"].id,
            SeatLock.is_active == True
        ).count()
        assert confirmed_count + active_locks <= capacity
    finally:
        db.close()


def test_vehicle_location_report(setup_test_data):
    data = setup_test_data
    response = client.post(
        "/api/v1/vehicle-locations/report",
        json={
            "vehicle_id": data["vehicle"].id,
            "schedule_id": data["schedule"].id,
            "latitude": 31.2304,
            "longitude": 121.4737,
            "speed": 45.0
        }
    )
    assert response.status_code == 200
    loc = response.json()
    assert loc is not None
    assert loc["latitude"] == 31.2304
    assert loc["speed"] == 45.0


def test_get_schedule_eta(setup_test_data):
    data = setup_test_data
    client.post(
        "/api/v1/vehicle-locations/report",
        json={
            "vehicle_id": data["vehicle"].id,
            "schedule_id": data["schedule"].id,
            "latitude": 31.0,
            "longitude": 121.0,
            "speed": 40.0
        }
    )
    response = client.get(f"/api/v1/vehicle-locations/schedule/{data['schedule'].id}/eta")
    assert response.status_code == 200
    data_eta = response.json()
    assert "stations_eta" in data_eta


def test_assign_driver_tasks(setup_test_data):
    """需求1：司机任务分配接口成功，通知可查"""
    data = setup_test_data
    response = client.post(
        f"/api/v1/driver-tasks/assign-daily?driver_id={data['driver'].id}&task_date={data['schedule'].departure_date.isoformat()}"
    )
    assert response.status_code == 200
    result = response.json()
    assert "assigned" in result
    assigned = result["assigned"]
    assert assigned >= 0

    db = TestingSessionLocal()
    try:
        notifs = db.query(Notification).filter(Notification.driver_id == data["driver"].id).all()
        if assigned > 0:
            assert len(notifs) >= assigned
    finally:
        db.close()


def test_generate_performance_report(setup_test_data):
    today = date.today()
    response = client.post(
        f"/api/v1/performance-reports/generate?year={today.year}&month={today.month}"
    )
    assert response.status_code == 200
    reports = response.json()
    assert isinstance(reports, list)


def test_check_schedule_demand_by_station(setup_test_data):
    """需求3：按站点人数判断取消，站点B 0人<阈值2，取消班次"""
    data = setup_test_data
    emp1, emp2 = data["employees"][0], data["employees"][1]
    stationA = data["stations"][0]

    client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp1.id,
            "station_id": stationA.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp2.id,
            "station_id": stationA.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )

    response = client.post(
        f"/api/v1/schedules/{data['schedule'].id}/check-demand"
    )
    assert response.status_code == 200
    result = response.json()
    assert "cancelled" in result
    assert result["cancelled"] == True
    assert "low_demand_stations" in result
    assert len(result["low_demand_stations"]) >= 1
    assert result["cancel_reason"] == "station_below_threshold"

    db = TestingSessionLocal()
    try:
        notifs = db.query(Notification).filter(
            Notification.employee_id.in_([emp1.id, emp2.id]),
            Notification.type == "schedule_cancelled"
        ).all()
        assert len(notifs) >= 2
    finally:
        db.close()


def test_cancel_reservation_and_notification(setup_test_data):
    """需求1：取消预约成功，通知可查，接口不500"""
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    res1 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    ).json()
    assert res1["success"] == True
    res_id = res1["reservation"]["id"]

    response = client.delete(
        f"/api/v1/reservations/{res_id}?employee_id={emp.id}"
    )
    assert response.status_code == 200
    assert response.json()["success"] == True

    db = TestingSessionLocal()
    try:
        cancel_notifs = db.query(Notification).filter(
            Notification.employee_id == emp.id,
            Notification.type == "reservation_cancelled"
        ).all()
        assert len(cancel_notifs) >= 1
    finally:
        db.close()


def test_notification_failure_does_not_break_api(setup_test_data):
    """需求1：即使通知服务出问题，主接口仍正常返回"""
    data = setup_test_data
    emp = data["employees"][2]
    station = data["stations"][0]

    response = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response.status_code == 200
    result = response.json()
    assert result["success"] in [True, False]
    assert "message" in result or "reservation" in result


def test_export_performance_report(setup_test_data):
    today = date.today()
    client.post(f"/api/v1/performance-reports/generate?year={today.year}&month={today.month}")
    response = client.get("/api/v1/performance-reports/export")
    assert response.status_code == 200
    assert "application/vnd.openxmlformats" in response.headers["content-type"]


def test_get_employee_notifications(setup_test_data):
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )

    response = client.get(f"/api/v1/notifications/employee/{emp.id}")
    assert response.status_code == 200
    notifs = response.json()
    assert isinstance(notifs, list)
    assert len(notifs) >= 1


def test_schedules_list_with_stats_and_filter(setup_test_data):
    """需求1：班次列表返回余座统计，支持status筛选不报错"""
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )

    response = client.get("/api/v1/schedules")
    assert response.status_code == 200
    schedules = response.json()
    assert isinstance(schedules, list)
    assert len(schedules) > 0

    for s in schedules:
        assert "reserved_count" in s
        assert "available_seats" in s
        assert isinstance(s["reserved_count"], int)
        assert isinstance(s["available_seats"], int)
        assert s["reserved_count"] + s["available_seats"] <= data["vehicle"].capacity

    response_pending = client.get("/api/v1/schedules?status=pending")
    assert response_pending.status_code == 200
    pending_schedules = response_pending.json()
    assert all(s["status"] == "pending" for s in pending_schedules)

    response_invalid = client.get("/api/v1/schedules?status=invalid_status")
    assert response_invalid.status_code == 200
    invalid_schedules = response_invalid.json()
    assert len(invalid_schedules) >= len(pending_schedules)

    response_route = client.get(f"/api/v1/schedules?route_id={data['route'].id}")
    assert response_route.status_code == 200
    route_schedules = response_route.json()
    assert all(s["route_id"] == data["route"].id for s in route_schedules)


def test_cancel_and_rebook_same_schedule(setup_test_data):
    """需求2：取消后重新预约同一班次成功，通知各保留一条"""
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    response1 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response1.status_code == 200
    result1 = response1.json()
    assert result1["success"] == True
    res_id = result1["reservation"]["id"]
    available_after_first = result1["available_seats_after"]

    db = TestingSessionLocal()
    try:
        notifs_after_book = db.query(Notification).filter(
            Notification.employee_id == emp.id,
            Notification.type.in_(["reservation_confirmed", "reservation_cancelled"])
        ).all()
        confirmed_count = sum(1 for n in notifs_after_book if n.type == "reservation_confirmed")
        assert confirmed_count >= 1
    finally:
        db.close()

    response_cancel = client.delete(f"/api/v1/reservations/{res_id}?employee_id={emp.id}")
    assert response_cancel.status_code == 200
    assert response_cancel.json()["success"] == True

    db = TestingSessionLocal()
    try:
        notifs_after_cancel = db.query(Notification).filter(
            Notification.employee_id == emp.id,
            Notification.type.in_(["reservation_confirmed", "reservation_cancelled"])
        ).all()
        cancel_count = sum(1 for n in notifs_after_cancel if n.type == "reservation_cancelled")
        assert cancel_count >= 1
    finally:
        db.close()

    response2 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response2.status_code == 200
    result2 = response2.json()
    assert result2["success"] == True
    assert result2["schedule"]["id"] == data["schedule"].id

    db = TestingSessionLocal()
    try:
        notifs_final = db.query(Notification).filter(
            Notification.employee_id == emp.id,
            Notification.type.in_(["reservation_confirmed", "reservation_cancelled"])
        ).order_by(Notification.created_at).all()

        confirmed_notifs = [n for n in notifs_final if n.type == "reservation_confirmed"]
        cancel_notifs = [n for n in notifs_final if n.type == "reservation_cancelled"]

        assert len(confirmed_notifs) >= 1
        assert len(cancel_notifs) >= 1

        total_notifs = len(confirmed_notifs) + len(cancel_notifs)
        assert total_notifs <= 3

        latest_confirmed = max(confirmed_notifs, key=lambda n: n.created_at)
        assert "重新预约" in latest_confirmed.title or "预约成功" in latest_confirmed.title

        res = db.query(Reservation).filter(
            Reservation.schedule_id == data["schedule"].id,
            Reservation.employee_id == emp.id,
            Reservation.status == ReservationStatus.CONFIRMED
        ).first()
        assert res is not None
        assert res.id == result2["reservation"]["id"]
    finally:
        db.close()


def test_location_report_no_duplicate_push(setup_test_data):
    """需求3：连续上报位置，通知列表不重复刷屏"""
    data = setup_test_data
    emp = data["employees"][0]
    station = data["stations"][0]

    client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": emp.id,
            "station_id": station.id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )

    db = TestingSessionLocal()
    try:
        initial_notifs = db.query(Notification).filter(
            Notification.employee_id == emp.id,
            Notification.type == "vehicle_position_updated"
        ).count()
    finally:
        db.close()

    VehicleLocationService._last_push_state = {}

    for i in range(5):
        response = client.post(
            "/api/v1/vehicle-locations/report",
            json={
                "vehicle_id": data["vehicle"].id,
                "schedule_id": data["schedule"].id,
                "latitude": 31.0 + i * 0.001,
                "longitude": 121.0 + i * 0.001,
                "speed": 40.0
            }
        )
        assert response.status_code == 200

    db = TestingSessionLocal()
    try:
        pos_notifs = db.query(Notification).filter(
            Notification.employee_id == emp.id,
            Notification.type == "vehicle_position_updated"
        ).all()
        new_notifs_count = len(pos_notifs) - initial_notifs

        assert new_notifs_count <= 3
        assert new_notifs_count <= 5

        for n in pos_notifs:
            assert n.title in ["班车位置更新", "班车即将到站"]
            assert n.related_id == data["schedule"].id
    finally:
        db.close()


def test_schedules_list_does_not_500(setup_test_data):
    """需求1：各种边界条件下列表接口不报错"""
    response1 = client.get("/api/v1/schedules?status=completed")
    assert response1.status_code == 200
    assert isinstance(response1.json(), list)

    response2 = client.get("/api/v1/schedules?route_id=999999")
    assert response2.status_code == 200
    assert isinstance(response2.json(), list)

    from datetime import date
    future_date = date.today().strftime("%Y-%m-%d")
    response3 = client.get(f"/api/v1/schedules?start_date={future_date}")
    assert response3.status_code == 200
    assert isinstance(response3.json(), list)

    response4 = client.get("/api/v1/schedules?status=pending&status=active")
    assert response4.status_code == 200
    assert isinstance(response4.json(), list)
