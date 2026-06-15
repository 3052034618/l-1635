from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date,
    ForeignKey, Text, UniqueConstraint, Index, Enum as SAEnum
)
from sqlalchemy.orm import relationship
import enum

from app.database import Base


class UserRole(str, enum.Enum):
    EMPLOYEE = "employee"
    DRIVER = "driver"
    ADMIN = "admin"


class ScheduleStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReservationStatus(str, enum.Enum):
    LOCKED = "locked"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class TaskStatus(str, enum.Enum):
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class NotificationType(str, enum.Enum):
    RESERVATION_CONFIRMED = "reservation_confirmed"
    RESERVATION_CANCELLED = "reservation_cancelled"
    SCHEDULE_CANCELLED = "schedule_cancelled"
    VEHICLE_POSITION_UPDATED = "vehicle_position_updated"
    TASK_ASSIGNED = "task_assigned"
    ALTERNATIVE_RECOMMENDED = "alternative_recommended"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    employee_no = Column(String(50), unique=True, nullable=False, index=True)
    department = Column(String(100))
    phone = Column(String(20))
    default_station_id = Column(Integer, ForeignKey("stations.id"))
    hashed_password = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    reservations = relationship("Reservation", back_populates="employee")
    notifications = relationship(
        "Notification", back_populates="employee", foreign_keys="Notification.employee_id"
    )


class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    driver_no = Column(String(50), unique=True, nullable=False, index=True)
    phone = Column(String(20))
    license_no = Column(String(50))
    hashed_password = Column(String(255))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    schedules = relationship("Schedule", back_populates="driver")
    tasks = relationship("DriverTask", back_populates="driver")
    notifications = relationship(
        "Notification", back_populates="driver", foreign_keys="Notification.driver_id"
    )


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    plate_no = Column(String(20), unique=True, nullable=False, index=True)
    model = Column(String(100))
    capacity = Column(Integer, nullable=False, default=30)
    fuel_type = Column(String(20))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    schedules = relationship("Schedule", back_populates="vehicle")
    locations = relationship("VehicleLocation", back_populates="vehicle")


class Station(Base):
    __tablename__ = "stations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    address = Column(String(255))
    latitude = Column(Float)
    longitude = Column(Float)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    route_stations = relationship("RouteStation", back_populates="station")


class Route(Base):
    __tablename__ = "routes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    direction = Column(String(20), default="up")
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    route_stations = relationship("RouteStation", back_populates="route", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="route")


class RouteStation(Base):
    __tablename__ = "route_stations"
    __table_args__ = (
        UniqueConstraint("route_id", "station_id", "sequence", name="uq_route_station_seq"),
    )

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    sequence = Column(Integer, nullable=False)
    arrival_offset_minutes = Column(Integer, nullable=False, default=0)

    route = relationship("Route", back_populates="route_stations")
    station = relationship("Station", back_populates="route_stations")


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("idx_schedule_route_date", "route_id", "departure_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    departure_time = Column(DateTime, nullable=False)
    departure_date = Column(Date, nullable=False)
    arrival_time = Column(DateTime)
    status = Column(SAEnum(ScheduleStatus), default=ScheduleStatus.PENDING)
    actual_departure_time = Column(DateTime)
    actual_arrival_time = Column(DateTime)
    min_passengers_threshold = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)

    route = relationship("Route", back_populates="schedules")
    vehicle = relationship("Vehicle", back_populates="schedules")
    driver = relationship("Driver", back_populates="schedules")
    reservations = relationship("Reservation", back_populates="schedule")
    tasks = relationship("DriverTask", back_populates="schedule")
    seat_locks = relationship("SeatLock", back_populates="schedule")


class SeatLock(Base):
    __tablename__ = "seat_locks"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    locked_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)

    schedule = relationship("Schedule", back_populates="seat_locks")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        Index("idx_reservation_employee_date", "employee_id", "reservation_date"),
        UniqueConstraint("schedule_id", "employee_id", name="uq_schedule_employee"),
    )

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    reservation_date = Column(Date, nullable=False)
    status = Column(SAEnum(ReservationStatus), default=ReservationStatus.CONFIRMED)
    seat_no = Column(String(10))
    boarded_at = Column(DateTime)
    alighted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    schedule = relationship("Schedule", back_populates="reservations")
    employee = relationship("Employee", back_populates="reservations")
    station = relationship("Station")


class VehicleLocation(Base):
    __tablename__ = "vehicle_locations"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id"))
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    speed = Column(Float, default=0)
    heading = Column(Float)
    reported_at = Column(DateTime, default=datetime.utcnow, index=True)

    vehicle = relationship("Vehicle", back_populates="locations")
    schedule = relationship("Schedule")


class DriverTask(Base):
    __tablename__ = "driver_tasks"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    task_date = Column(Date, nullable=False)
    status = Column(SAEnum(TaskStatus), default=TaskStatus.ASSIGNED)
    start_mileage = Column(Float)
    end_mileage = Column(Float)
    fuel_consumption = Column(Float)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver", back_populates="tasks")
    schedule = relationship("Schedule", back_populates="tasks")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(SAEnum(NotificationType), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"))
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    related_id = Column(Integer)
    related_type = Column(String(50))
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("Employee", back_populates="notifications", foreign_keys=[employee_id])
    driver = relationship("Driver", back_populates="notifications", foreign_keys=[driver_id])


class PerformanceReport(Base):
    __tablename__ = "performance_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_month = Column(String(7), nullable=False, index=True)
    route_id = Column(Integer, ForeignKey("routes.id"), nullable=False)
    total_schedules = Column(Integer, default=0)
    completed_schedules = Column(Integer, default=0)
    cancelled_schedules = Column(Integer, default=0)
    on_time_schedules = Column(Integer, default=0)
    total_passengers = Column(Integer, default=0)
    total_capacity = Column(Integer, default=0)
    total_mileage = Column(Float, default=0)
    total_fuel = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    route = relationship("Route")

    @property
    def completion_rate(self):
        return (self.completed_schedules / self.total_schedules * 100) if self.total_schedules > 0 else 0

    @property
    def on_time_rate(self):
        return (self.on_time_schedules / self.completed_schedules * 100) if self.completed_schedules > 0 else 0

    @property
    def load_rate(self):
        return (self.total_passengers / self.total_capacity * 100) if self.total_capacity > 0 else 0
