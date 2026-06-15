from datetime import datetime, date
from typing import List, Optional, Dict
from io import BytesIO
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models import (
    PerformanceReport, Schedule, ScheduleStatus,
    Reservation, ReservationStatus, Route, DriverTask, TaskStatus
)
from app.services.schedule_management import ScheduleManagementService
import pandas as pd


class PerformanceReportService:
    def __init__(self, db: Session):
        self.db = db
        self.schedule_mgmt = ScheduleManagementService(db)

    def _get_month_range(self, year: int, month: int):
        from calendar import monthrange
        _, last_day = monthrange(year, month)
        start_date = date(year, month, 1)
        end_date = date(year, month, last_day)
        return start_date, end_date, f"{year:04d}-{month:02d}"

    def generate_monthly_report(self, year: int, month: int, route_id: Optional[int] = None) -> List[PerformanceReport]:
        start_date, end_date, month_str = self._get_month_range(year, month)

        routes_query = self.db.query(Route).filter(Route.is_active == True)
        if route_id:
            routes_query = routes_query.filter(Route.id == route_id)
        routes = routes_query.all()

        reports = []
        for route in routes:
            report = self._calculate_route_stats(route, start_date, end_date, month_str)
            reports.append(report)

        return reports

    def _calculate_route_stats(
        self, route: Route, start_date: date, end_date: date, month_str: str
    ) -> PerformanceReport:
        existing = self.db.query(PerformanceReport).filter(
            PerformanceReport.report_month == month_str,
            PerformanceReport.route_id == route.id
        ).first()

        if existing:
            self.db.delete(existing)
            self.db.commit()

        schedules = self.db.query(Schedule).filter(
            Schedule.route_id == route.id,
            Schedule.departure_date >= start_date,
            Schedule.departure_date <= end_date
        ).all()

        total_schedules = len(schedules)
        completed_schedules = len([s for s in schedules if s.status == ScheduleStatus.COMPLETED])
        cancelled_schedules = len([s for s in schedules if s.status == ScheduleStatus.CANCELLED])

        on_time_schedules = 0
        for sched in schedules:
            if sched.status == ScheduleStatus.COMPLETED and self.schedule_mgmt.is_on_time(sched):
                on_time_schedules += 1

        completed_schedule_ids = [s.id for s in schedules if s.status == ScheduleStatus.COMPLETED]

        total_passengers = 0
        total_capacity = 0
        if completed_schedule_ids:
            total_passengers = self.db.query(func.count(Reservation.id)).filter(
                Reservation.schedule_id.in_(completed_schedule_ids),
                Reservation.status == ReservationStatus.CONFIRMED
            ).scalar() or 0

            for sched in schedules:
                if sched.status == ScheduleStatus.COMPLETED and sched.vehicle:
                    total_capacity += sched.vehicle.capacity

        total_mileage = 0
        total_fuel = 0
        if completed_schedule_ids:
            tasks = self.db.query(DriverTask).filter(
                DriverTask.schedule_id.in_(completed_schedule_ids),
                DriverTask.status == TaskStatus.COMPLETED
            ).all()
            for task in tasks:
                if task.end_mileage and task.start_mileage:
                    total_mileage += task.end_mileage - task.start_mileage
                if task.fuel_consumption:
                    total_fuel += task.fuel_consumption

        report = PerformanceReport(
            report_month=month_str,
            route_id=route.id,
            total_schedules=total_schedules,
            completed_schedules=completed_schedules,
            cancelled_schedules=cancelled_schedules,
            on_time_schedules=on_time_schedules,
            total_passengers=total_passengers,
            total_capacity=total_capacity,
            total_mileage=round(total_mileage, 2),
            total_fuel=round(total_fuel, 2)
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)

        return report

    def get_reports(self, report_month: Optional[str] = None, route_id: Optional[int] = None) -> List[PerformanceReport]:
        query = self.db.query(PerformanceReport)
        if report_month:
            query = query.filter(PerformanceReport.report_month == report_month)
        if route_id:
            query = query.filter(PerformanceReport.route_id == route_id)
        return query.order_by(PerformanceReport.report_month.desc()).all()

    def export_to_excel(self, report_month: Optional[str] = None, route_id: Optional[int] = None) -> BytesIO:
        reports = self.get_reports(report_month, route_id)

        data = []
        for r in reports:
            data.append({
                "月份": r.report_month,
                "线路": r.route.name if r.route else "未知",
                "计划班次": r.total_schedules,
                "完成班次": r.completed_schedules,
                "取消班次": r.cancelled_schedules,
                "准点班次": r.on_time_schedules,
                "开通率(%)": round(r.completion_rate, 2),
                "准点率(%)": round(r.on_time_rate, 2),
                "载客率(%)": round(r.load_rate, 2),
                "总载客数": r.total_passengers,
                "总运力": r.total_capacity,
                "总里程(km)": r.total_mileage,
                "总油耗(L)": r.total_fuel
            })

        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='绩效报表', index=False)
        output.seek(0)
        return output

    def get_summary(self, report_month: str) -> Dict:
        reports = self.get_reports(report_month=report_month)

        total_schedules = sum(r.total_schedules for r in reports)
        total_completed = sum(r.completed_schedules for r in reports)
        total_on_time = sum(r.on_time_schedules for r in reports)
        total_passengers = sum(r.total_passengers for r in reports)
        total_capacity = sum(r.total_capacity for r in reports)
        total_mileage = sum(r.total_mileage for r in reports)
        total_fuel = sum(r.total_fuel for r in reports)

        avg_completion = (total_completed / total_schedules * 100) if total_schedules > 0 else 0
        avg_ontime = (total_on_time / total_completed * 100) if total_completed > 0 else 0
        avg_load = (total_passengers / total_capacity * 100) if total_capacity > 0 else 0

        return {
            "report_month": report_month,
            "total_routes": len(reports),
            "total_schedules": total_schedules,
            "total_completed": total_completed,
            "avg_completion_rate": round(avg_completion, 2),
            "avg_on_time_rate": round(avg_ontime, 2),
            "avg_load_rate": round(avg_load, 2),
            "total_passengers": total_passengers,
            "total_mileage_km": round(total_mileage, 2),
            "total_fuel_liters": round(total_fuel, 2)
        }


def get_performance_report_service(db: Session) -> PerformanceReportService:
    return PerformanceReportService(db)
