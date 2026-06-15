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
    Schedule, ScheduleStatus, Reservation, ReservationStatus
)
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

        employee = Employee(
            name="测试员工", employee_no="EMP001", department="测试部",
            default_station_id=station1.id,
            hashed_password=pwd_context.hash("123456")
        )
        db.add(employee)
        db.flush()

        driver = Driver(
            name="测试司机", driver_no="DRV001",
            hashed_password=pwd_context.hash("123456")
        )
        db.add(driver)
        db.flush()

        vehicle = Vehicle(plate_no="沪A·TEST01", model="测试车型", capacity=30)
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
            "employee": employee,
            "driver": driver,
            "vehicle": vehicle,
            "schedule": schedule
        }
    finally:
        db.close()


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "企业班车智能调度系统" in response.json()["name"]


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


def test_smart_reservation(setup_test_data):
    data = setup_test_data
    response = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": data["employee"].id,
            "station_id": data["stations"][0].id,
            "target_date": data["schedule"].departure_date.isoformat(),
            "direction": "up"
        }
    )
    assert response.status_code == 200
    result = response.json()
    assert result["success"] == True
    assert "reservation" in result


def test_conflict_detection(setup_test_data):
    data = setup_test_data
    response1 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": data["employee"].id,
            "station_id": data["stations"][0].id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response1.status_code == 200
    assert response1.json()["success"] == True

    response2 = client.post(
        "/api/v1/reservations/smart",
        json={
            "employee_id": data["employee"].id,
            "station_id": data["stations"][0].id,
            "target_date": data["schedule"].departure_date.isoformat(),
        }
    )
    assert response2.status_code == 200
    result = response2.json()
    assert result.get("has_conflict") == True or result.get("success") == False


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
    data = setup_test_data
    response = client.post(
        f"/api/v1/driver-tasks/assign-daily?driver_id={data['driver'].id}&task_date={data['schedule'].departure_date.isoformat()}"
    )
    assert response.status_code == 200
    result = response.json()
    assert "assigned" in result


def test_generate_performance_report(setup_test_data):
    today = date.today()
    response = client.post(
        f"/api/v1/performance-reports/generate?year={today.year}&month={today.month}"
    )
    assert response.status_code == 200
    reports = response.json()
    assert isinstance(reports, list)


def test_check_schedule_demand(setup_test_data):
    data = setup_test_data
    response = client.post(
        f"/api/v1/schedules/{data['schedule'].id}/check-demand"
    )
    assert response.status_code == 200
    result = response.json()
    assert "cancelled" in result
    assert result["cancelled"] == True


def test_export_performance_report(setup_test_data):
    today = date.today()
    client.post(f"/api/v1/performance-reports/generate?year={today.year}&month={today.month}")
    response = client.get("/api/v1/performance-reports/export")
    assert response.status_code == 200
    assert "application/vnd.openxmlformats" in response.headers["content-type"]
