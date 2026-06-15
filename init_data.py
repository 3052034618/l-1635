from datetime import datetime, timedelta, date
from app.database import SessionLocal, engine, Base
from app.models import (
    Station, Route, RouteStation, Employee, Driver, Vehicle,
    Schedule, ScheduleStatus
)
from passlib.context import CryptContext


def init_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    try:
        print("开始初始化测试数据...")

        stations = [
            Station(name="公司总部站", address="科技园区A座", latitude=31.2304, longitude=121.4737),
            Station(name="人民广场站", address="人民广场地铁站", latitude=31.2325, longitude=121.4733),
            Station(name="徐家汇站", address="徐家汇地铁站", latitude=31.1929, longitude=121.4365),
            Station(name="张江站", address="张江高科地铁站", latitude=31.2097, longitude=121.5978),
            Station(name="龙阳路站", address="龙阳路地铁站", latitude=31.2181, longitude=121.5539),
            Station(name="莘庄站", address="莘庄地铁站", latitude=31.1101, longitude=121.3844),
        ]
        for s in stations:
            db.add(s)
        db.flush()
        print(f"已创建 {len(stations)} 个站点")

        route1_stations = [
            RouteStation(station_id=stations[0].id, sequence=1, arrival_offset_minutes=0),
            RouteStation(station_id=stations[2].id, sequence=2, arrival_offset_minutes=15),
            RouteStation(station_id=stations[1].id, sequence=3, arrival_offset_minutes=30),
            RouteStation(station_id=stations[4].id, sequence=4, arrival_offset_minutes=45),
            RouteStation(station_id=stations[3].id, sequence=5, arrival_offset_minutes=60),
        ]
        route2_stations = [
            RouteStation(station_id=stations[0].id, sequence=1, arrival_offset_minutes=0),
            RouteStation(station_id=stations[1].id, sequence=2, arrival_offset_minutes=20),
            RouteStation(station_id=stations[5].id, sequence=3, arrival_offset_minutes=40),
        ]

        routes = [
            Route(name="张江早班线", code="R001", direction="up", description="公司总部至张江方向"),
            Route(name="莘庄早班线", code="R002", direction="up", description="公司总部至莘庄方向"),
        ]
        for r in routes:
            db.add(r)
        db.flush()

        for i, rs in enumerate(route1_stations):
            rs.route_id = routes[0].id
            db.add(rs)
        for i, rs in enumerate(route2_stations):
            rs.route_id = routes[1].id
            db.add(rs)
        db.flush()
        print(f"已创建 {len(routes)} 条线路")

        employees = []
        for i in range(1, 11):
            emp = Employee(
                name=f"员工{i:02d}",
                employee_no=f"EMP{i:04d}",
                department="技术部" if i <= 5 else "市场部",
                phone=f"1380000{i:04d}",
                default_station_id=stations[i % len(stations)].id,
                hashed_password=pwd_context.hash("123456")
            )
            employees.append(emp)
            db.add(emp)
        db.flush()
        print(f"已创建 {len(employees)} 名员工")

        drivers = []
        for i in range(1, 4):
            d = Driver(
                name=f"司机{i:02d}",
                driver_no=f"DRV{i:04d}",
                phone=f"1390000{i:04d}",
                license_no=f"A1{i:06d}",
                hashed_password=pwd_context.hash("123456")
            )
            drivers.append(d)
            db.add(d)
        db.flush()
        print(f"已创建 {len(drivers)} 名司机")

        vehicles = []
        for i in range(1, 4):
            v = Vehicle(
                plate_no=f"沪A·{i:05d}",
                model=f"宇通客车{i}型",
                capacity=30,
                fuel_type="柴油"
            )
            vehicles.append(v)
            db.add(v)
        db.flush()
        print(f"已创建 {len(vehicles)} 辆车")

        today = date.today()
        schedules = []
        base_time = datetime.combine(today, datetime.min.time()).replace(hour=7, minute=30)

        for day_offset in range(3):
            for route_idx, route in enumerate(routes):
                for trip in range(2):
                    departure = base_time + timedelta(days=day_offset, hours=trip * 2 + route_idx)
                    arrival = departure + timedelta(minutes=75)
                    sched = Schedule(
                        route_id=route.id,
                        vehicle_id=vehicles[route_idx % len(vehicles)].id,
                        driver_id=drivers[route_idx % len(drivers)].id,
                        departure_time=departure,
                        departure_date=departure.date(),
                        arrival_time=arrival,
                        status=ScheduleStatus.PENDING,
                        min_passengers_threshold=3
                    )
                    schedules.append(sched)
                    db.add(sched)
        db.flush()
        print(f"已创建 {len(schedules)} 个班次")

        db.commit()
        print("测试数据初始化完成！")
        print("\n默认账号密码:")
        print("  员工: EMP0001 - 123456")
        print("  司机: DRV0001 - 123456")

    except Exception as e:
        db.rollback()
        print(f"初始化失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
