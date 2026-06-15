from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Station, Route, RouteStation, Employee, Driver, Vehicle
from app.schemas import (
    StationCreate, Station as StationSchema,
    RouteCreate, Route as RouteSchema,
    EmployeeCreate, Employee as EmployeeSchema,
    DriverCreate, Driver as DriverSchema,
    VehicleCreate, Vehicle as VehicleSchema
)

router = APIRouter(tags=["基础数据"])


@router.post("/stations", response_model=StationSchema, summary="创建站点")
def create_station(data: StationCreate, db: Session = Depends(get_db)):
    station = Station(**data.model_dump())
    db.add(station)
    db.commit()
    db.refresh(station)
    return station


@router.get("/stations", response_model=List[StationSchema], summary="获取站点列表")
def list_stations(db: Session = Depends(get_db)):
    return db.query(Station).filter(Station.is_active == True).all()


@router.post("/routes", response_model=RouteSchema, summary="创建线路")
def create_route(data: RouteCreate, db: Session = Depends(get_db)):
    route = Route(
        name=data.name,
        code=data.code,
        direction=data.direction,
        description=data.description
    )
    db.add(route)
    db.flush()

    for rs_data in data.stations:
        route_station = RouteStation(
            route_id=route.id,
            station_id=rs_data.station_id,
            sequence=rs_data.sequence,
            arrival_offset_minutes=rs_data.arrival_offset_minutes
        )
        db.add(route_station)

    db.commit()
    db.refresh(route)
    return route


@router.get("/routes", response_model=List[RouteSchema], summary="获取线路列表")
def list_routes(db: Session = Depends(get_db)):
    return db.query(Route).filter(Route.is_active == True).all()


@router.post("/employees", response_model=EmployeeSchema, summary="创建员工")
def create_employee(data: EmployeeCreate, db: Session = Depends(get_db)):
    existing = db.query(Employee).filter(Employee.employee_no == data.employee_no).first()
    if existing:
        raise HTTPException(status_code=400, detail="员工工号已存在")
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    employee = Employee(
        name=data.name,
        employee_no=data.employee_no,
        department=data.department,
        phone=data.phone,
        default_station_id=data.default_station_id,
        hashed_password=pwd_context.hash(data.password)
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@router.get("/employees", response_model=List[EmployeeSchema], summary="获取员工列表")
def list_employees(department: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Employee).filter(Employee.is_active == True)
    if department:
        query = query.filter(Employee.department == department)
    return query.all()


@router.post("/drivers", response_model=DriverSchema, summary="创建司机")
def create_driver(data: DriverCreate, db: Session = Depends(get_db)):
    existing = db.query(Driver).filter(Driver.driver_no == data.driver_no).first()
    if existing:
        raise HTTPException(status_code=400, detail="司机工号已存在")
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    driver = Driver(
        name=data.name,
        driver_no=data.driver_no,
        phone=data.phone,
        license_no=data.license_no,
        hashed_password=pwd_context.hash(data.password)
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return driver


@router.get("/drivers", response_model=List[DriverSchema], summary="获取司机列表")
def list_drivers(db: Session = Depends(get_db)):
    return db.query(Driver).filter(Driver.is_active == True).all()


@router.post("/vehicles", response_model=VehicleSchema, summary="创建车辆")
def create_vehicle(data: VehicleCreate, db: Session = Depends(get_db)):
    existing = db.query(Vehicle).filter(Vehicle.plate_no == data.plate_no).first()
    if existing:
        raise HTTPException(status_code=400, detail="车牌号已存在")
    vehicle = Vehicle(**data.model_dump())
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.get("/vehicles", response_model=List[VehicleSchema], summary="获取车辆列表")
def list_vehicles(db: Session = Depends(get_db)):
    return db.query(Vehicle).filter(Vehicle.is_active == True).all()
