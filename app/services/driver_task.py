from datetime import datetime, date, timedelta
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from app.models import (
    Driver, DriverTask, TaskStatus, Schedule, ScheduleStatus
)
from app.schemas import DriverTaskComplete
from app.services.notification import notification_service


class DriverTaskService:
    def __init__(self, db: Session):
        self.db = db

    def assign_daily_tasks(self, driver_id: int, task_date: date = None) -> List[DriverTask]:
        if task_date is None:
            task_date = date.today()

        driver = self.db.query(Driver).filter(Driver.id == driver_id).first()
        if not driver:
            return []

        existing_tasks = self.db.query(DriverTask).filter(
            DriverTask.driver_id == driver_id,
            DriverTask.task_date == task_date
        ).all()
        existing_schedule_ids = [t.schedule_id for t in existing_tasks]

        query = self.db.query(Schedule).filter(
            Schedule.driver_id == driver_id,
            Schedule.departure_date == task_date,
            Schedule.status.in_([ScheduleStatus.PENDING, ScheduleStatus.ACTIVE])
        )
        if existing_schedule_ids:
            query = query.filter(~Schedule.id.in_(existing_schedule_ids))
        schedules = query.all()

        created_tasks = []
        for sched in schedules:
            task = DriverTask(
                driver_id=driver_id,
                schedule_id=sched.id,
                task_date=task_date,
                status=TaskStatus.ASSIGNED
            )
            self.db.add(task)
            created_tasks.append(task)

            notification_service.create_notification(
                self.db,
                type="task_assigned",
                driver_id=driver_id,
                title="新任务分配",
                content=f"您已分配到新任务：{sched.departure_time.strftime('%Y-%m-%d %H:%M')} 发车",
                related_id=sched.id,
                related_type="schedule"
            )

        self.db.commit()
        for task in created_tasks:
            self.db.refresh(task)

        return created_tasks

    def auto_assign_all_drivers(self, task_date: date = None) -> Dict:
        if task_date is None:
            task_date = date.today()

        schedules = self.db.query(Schedule).filter(
            Schedule.departure_date == task_date,
            Schedule.driver_id.isnot(None),
            Schedule.status.in_([ScheduleStatus.PENDING, ScheduleStatus.ACTIVE])
        ).all()

        result = {"total_schedules": len(schedules), "tasks_created": 0}

        driver_schedules = {}
        for sched in schedules:
            if sched.driver_id not in driver_schedules:
                driver_schedules[sched.driver_id] = []
            driver_schedules[sched.driver_id].append(sched)

        for driver_id, sched_list in driver_schedules.items():
            created = self.assign_daily_tasks(driver_id, task_date)
            result["tasks_created"] += len(created)

        return result

    def start_task(self, task_id: int, start_mileage: float) -> Optional[DriverTask]:
        task = self.db.query(DriverTask).filter(DriverTask.id == task_id).first()
        if not task or task.status != TaskStatus.ASSIGNED:
            return None

        task.status = TaskStatus.IN_PROGRESS
        task.start_mileage = start_mileage
        task.started_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(task)
        return task

    def complete_task(self, task_id: int, data: DriverTaskComplete) -> Optional[DriverTask]:
        task = self.db.query(DriverTask).filter(DriverTask.id == task_id).first()
        if not task or task.status != TaskStatus.IN_PROGRESS:
            return None

        task.status = TaskStatus.COMPLETED
        task.end_mileage = data.end_mileage
        task.fuel_consumption = data.fuel_consumption
        task.completed_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_driver_tasks(self, driver_id: int, task_date: date = None) -> List[DriverTask]:
        query = self.db.query(DriverTask).filter(DriverTask.driver_id == driver_id)
        if task_date:
            query = query.filter(DriverTask.task_date == task_date)
        return query.order_by(DriverTask.task_date.desc()).all()

    def get_tasks_by_date(self, task_date: date) -> List[DriverTask]:
        return self.db.query(DriverTask).filter(
            DriverTask.task_date == task_date
        ).order_by(DriverTask.driver_id).all()

    def get_task_stats(self, driver_id: int, start_date: date, end_date: date) -> Dict:
        tasks = self.db.query(DriverTask).filter(
            DriverTask.driver_id == driver_id,
            DriverTask.task_date >= start_date,
            DriverTask.task_date <= end_date,
            DriverTask.status == TaskStatus.COMPLETED
        ).all()

        total_mileage = sum(t.end_mileage - t.start_mileage for t in tasks if t.end_mileage and t.start_mileage)
        total_fuel = sum(t.fuel_consumption for t in tasks if t.fuel_consumption)
        avg_fuel_per_100km = (total_fuel / (total_mileage / 100)) if total_mileage > 0 else 0

        return {
            "driver_id": driver_id,
            "period": f"{start_date} ~ {end_date}",
            "total_tasks": len(tasks),
            "total_mileage_km": round(total_mileage, 2),
            "total_fuel_liters": round(total_fuel, 2),
            "avg_fuel_per_100km": round(avg_fuel_per_100km, 2)
        }


def get_driver_task_service(db: Session) -> DriverTaskService:
    return DriverTaskService(db)
