from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class StationBase(BaseModel):
    name: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class StationCreate(StationBase):
    pass


class Station(StationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class RouteStationBase(BaseModel):
    station_id: int
    sequence: int
    arrival_offset_minutes: int = 0


class RouteStationCreate(RouteStationBase):
    pass


class RouteStation(RouteStationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    station: Station


class RouteBase(BaseModel):
    name: str
    code: str
    direction: str = "up"
    description: Optional[str] = None


class RouteCreate(RouteBase):
    stations: List[RouteStationCreate]


class Route(RouteBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool = True
    route_stations: List[RouteStation] = []


class VehicleBase(BaseModel):
    plate_no: str
    model: Optional[str] = None
    capacity: int = 30
    fuel_type: Optional[str] = None


class VehicleCreate(VehicleBase):
    pass


class Vehicle(VehicleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool = True


class EmployeeBase(BaseModel):
    name: str
    employee_no: str
    department: Optional[str] = None
    phone: Optional[str] = None
    default_station_id: Optional[int] = None


class EmployeeCreate(EmployeeBase):
    password: str


class Employee(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool = True


class DriverBase(BaseModel):
    name: str
    driver_no: str
    phone: Optional[str] = None
    license_no: Optional[str] = None


class DriverCreate(DriverBase):
    password: str


class Driver(DriverBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool = True


class ScheduleBase(BaseModel):
    route_id: int
    vehicle_id: Optional[int] = None
    driver_id: Optional[int] = None
    departure_time: datetime
    arrival_time: Optional[datetime] = None
    min_passengers_threshold: int = 3


class ScheduleCreate(ScheduleBase):
    pass


class Schedule(ScheduleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    departure_date: date
    route: Optional[Route] = None
    vehicle: Optional[Vehicle] = None
    driver: Optional[Driver] = None


class ReservationBase(BaseModel):
    schedule_id: int
    employee_id: int
    station_id: int


class ReservationCreate(ReservationBase):
    pass


class SmartReservationRequest(BaseModel):
    employee_id: int
    station_id: int
    target_date: date
    preferred_time: Optional[datetime] = None
    direction: Optional[str] = None


class AlternativeSchedule(BaseModel):
    schedule: Schedule
    score: float
    reason: str


class ReservationConflictResponse(BaseModel):
    has_conflict: bool
    conflict_reservation: Optional["Reservation"] = None
    alternatives: List[AlternativeSchedule] = []


class Reservation(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    schedule_id: int
    employee_id: int
    station_id: int
    reservation_date: date
    status: str
    seat_no: Optional[str] = None
    schedule: Optional[Schedule] = None
    employee: Optional[Employee] = None
    station: Optional[Station] = None


ReservationConflictResponse.model_rebuild()


class VehicleLocationBase(BaseModel):
    vehicle_id: int
    schedule_id: Optional[int] = None
    latitude: float
    longitude: float
    speed: Optional[float] = 0
    heading: Optional[float] = None


class VehicleLocationCreate(VehicleLocationBase):
    pass


class VehicleLocation(VehicleLocationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reported_at: datetime
    eta_to_next_station: Optional[dict] = None


class DriverTaskBase(BaseModel):
    driver_id: int
    schedule_id: int
    task_date: date


class DriverTaskCreate(DriverTaskBase):
    pass


class DriverTaskComplete(BaseModel):
    end_mileage: float
    fuel_consumption: float


class DriverTask(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    driver_id: int
    schedule_id: int
    task_date: date
    status: str
    start_mileage: Optional[float] = None
    end_mileage: Optional[float] = None
    fuel_consumption: Optional[float] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    driver: Optional[Driver] = None
    schedule: Optional[Schedule] = None


class NotificationBase(BaseModel):
    type: str
    title: str
    content: str
    employee_id: Optional[int] = None
    driver_id: Optional[int] = None
    related_id: Optional[int] = None
    related_type: Optional[str] = None


class Notification(NotificationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_read: bool
    created_at: datetime


class PerformanceReportBase(BaseModel):
    report_month: str
    route_id: int
    total_schedules: int = 0
    completed_schedules: int = 0
    cancelled_schedules: int = 0
    on_time_schedules: int = 0
    total_passengers: int = 0
    total_capacity: int = 0
    total_mileage: float = 0
    total_fuel: float = 0


class PerformanceReport(PerformanceReportBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    completion_rate: float
    on_time_rate: float
    load_rate: float
    created_at: datetime
    route: Optional[Route] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ScheduleWithStats(Schedule):
    reserved_count: int = 0
    available_seats: int = 0
