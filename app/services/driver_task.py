from datetime import datetime, date, timedelta
from typing import List, Optional, Dict
import logging
from sqlalchemy.orm import Session

from app.models import (
    Driver, DriverTask, TaskStatus, Schedule, ScheduleStatus
)
from app.schemas import DriverTaskComplete
from app.services.notification import notification_service

logger = logging.getLogger(__name__)


class DriverTaskService:
    def __init__(self, db: Session):
        self.db = db

    def assign_daily_tasks(self, driver_id: int, task_date: date = None) -> List[DriverTask]:
        try:
            if task_date is None:
                task_date = date.today()

            driver = self.db.query(Driver).filter(Driver.id == driver_id).first()
            if not driver:
                logger.warning(f"司机[{driver_id}]不存在，无法分配任务")
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
                try:
                    task = DriverTask(
                        driver_id=driver_id,
                        schedule_id=sched.id,
                        task_date=task_date,
                        status=TaskStatus.ASSIGNED
                    )
                    self.db.add(task)
                    self.db.flush()
                    created_tasks.append(task)

                    try:
                        dep_time = sched.departure_time.strftime('%Y-%m-%d %H:%M')
                        notification_service.create_notification(
                            self.db,
                            type="task_assigned",
                            driver_id=driver_id,
                            title="新任务分配",
                            content=f"您已分配到新任务：{dep_time} 发车",
                            related_id=sched.id,
                            related_type="schedule",
                            extra={
                                "task_id": task.id,
                                "schedule_id": sched.id,
                                "departure_time": dep_time
                            }
                        )
                    except Exception as e:
                        logger.warning(f"发送任务分配通知给司机[{driver_id}]失败（不影响任务分配）: {e}")

                except Exception as e:
                    logger.warning(f"为班次[{sched.id}]创建任务失败: {e}")
                    continue

            self.db.commit()
            for task in created_tasks:
                try:
                    self.db.refresh(task)
                except:
                    pass

            logger.info(f"为司机[{driver_id}]分配了 {len(created_tasks)} 个任务")
            return created_tasks
        except Exception as e:
            logger.error(f"分配司机任务异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return []

    def auto_assign_all_drivers(self, task_date: date = None) -> Dict:
        try:
            if task_date is None:
                task_date = date.today()

            schedules = self.db.query(Schedule).filter(
                Schedule.departure_date == task_date,
                Schedule.driver_id.isnot(None),
                Schedule.status.in_([ScheduleStatus.PENDING, ScheduleStatus.ACTIVE])
            ).all()

            result = {"total_schedules": len(schedules), "tasks_created": 0, "date": str(task_date)}

            driver_schedules = {}
            for sched in schedules:
                if sched.driver_id not in driver_schedules:
                    driver_schedules[sched.driver_id] = []
                driver_schedules[sched.driver_id].append(sched)

            for driver_id, sched_list in driver_schedules.items():
                created = self.assign_daily_tasks(driver_id, task_date)
                result["tasks_created"] += len(created)

            logger.info(f"批量分配任务完成: {result}")
            return result
        except Exception as e:
            logger.error(f"批量分配所有司机任务异常: {e}", exc_info=True)
            return {"total_schedules": 0, "tasks_created": 0, "error": str(e)}

    def start_task(self, task_id: int, start_mileage: float) -> Optional[DriverTask]:
        try:
            task = self.db.query(DriverTask).filter(DriverTask.id == task_id).first()
            if not task:
                logger.warning(f"任务[{task_id}]不存在")
                return None
            if task.status != TaskStatus.ASSIGNED:
                logger.warning(f"任务[{task_id}]状态[{task.status}]非ASSIGNED，无法开始")
                return None

            task.status = TaskStatus.IN_PROGRESS
            task.start_mileage = start_mileage
            task.started_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(task)

            try:
                if task.driver_id and task.schedule:
                    dep_time = task.schedule.departure_time.strftime('%Y-%m-%d %H:%M')
                    notification_service.create_notification(
                        self.db,
                        type="task_assigned",
                        driver_id=task.driver_id,
                        title="任务已开始",
                        content=f"您的任务 {dep_time} 已开始，起始里程: {start_mileage} km",
                        related_id=task.id,
                        related_type="driver_task",
                        extra={"task_id": task.id, "start_mileage": start_mileage}
                    )
            except Exception as e:
                logger.warning(f"发送任务开始通知失败: {e}")

            return task
        except Exception as e:
            logger.error(f"开始任务异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def complete_task(self, task_id: int, data: DriverTaskComplete) -> Optional[DriverTask]:
        try:
            task = self.db.query(DriverTask).filter(DriverTask.id == task_id).first()
            if not task:
                logger.warning(f"任务[{task_id}]不存在")
                return None
            if task.status != TaskStatus.IN_PROGRESS:
                logger.warning(f"任务[{task_id}]状态[{task.status}]非IN_PROGRESS，无法完成")
                return None

            task.status = TaskStatus.COMPLETED
            task.end_mileage = data.end_mileage
            task.fuel_consumption = data.fuel_consumption
            task.completed_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(task)

            try:
                if task.driver_id:
                    mileage = 0
                    if task.end_mileage and task.start_mileage:
                        mileage = task.end_mileage - task.start_mileage
                    notification_service.create_notification(
                        self.db,
                        type="task_assigned",
                        driver_id=task.driver_id,
                        title="任务已完成",
                        content=f"任务完成！本次行驶 {mileage:.1f} km，油耗 {data.fuel_consumption:.1f} L",
                        related_id=task.id,
                        related_type="driver_task",
                        extra={
                            "task_id": task.id,
                            "mileage": mileage,
                            "fuel": data.fuel_consumption
                        }
                    )
            except Exception as e:
                logger.warning(f"发送任务完成通知失败: {e}")

            return task
        except Exception as e:
            logger.error(f"完成任务异常: {e}", exc_info=True)
            try:
                self.db.rollback()
            except:
                pass
            return None

    def get_driver_tasks(self, driver_id: int, task_date: date = None) -> List[DriverTask]:
        try:
            query = self.db.query(DriverTask).filter(DriverTask.driver_id == driver_id)
            if task_date:
                query = query.filter(DriverTask.task_date == task_date)
            return query.order_by(DriverTask.task_date.desc()).all()
        except Exception as e:
            logger.error(f"获取司机任务列表失败: {e}")
            return []

    def get_tasks_by_date(self, task_date: date) -> List[DriverTask]:
        try:
            return self.db.query(DriverTask).filter(
                DriverTask.task_date == task_date
            ).order_by(DriverTask.driver_id).all()
        except Exception as e:
            logger.error(f"按日期获取任务失败: {e}")
            return []

    def get_task_stats(self, driver_id: int, start_date: date, end_date: date) -> Dict:
        try:
            tasks = self.db.query(DriverTask).filter(
                DriverTask.driver_id == driver_id,
                DriverTask.task_date >= start_date,
                DriverTask.task_date <= end_date,
                DriverTask.status == TaskStatus.COMPLETED
            ).all()

            total_mileage = sum(
                t.end_mileage - t.start_mileage
                for t in tasks
                if t.end_mileage is not None and t.start_mileage is not None
            )
            total_fuel = sum(t.fuel_consumption for t in tasks if t.fuel_consumption is not None)
            avg_fuel_per_100km = (total_fuel / (total_mileage / 100)) if total_mileage > 0 else 0

            return {
                "driver_id": driver_id,
                "period": f"{start_date} ~ {end_date}",
                "total_tasks": len(tasks),
                "total_mileage_km": round(total_mileage, 2),
                "total_fuel_liters": round(total_fuel, 2),
                "avg_fuel_per_100km": round(avg_fuel_per_100km, 2)
            }
        except Exception as e:
            logger.error(f"获取司机统计失败: {e}", exc_info=True)
            return {
                "driver_id": driver_id,
                "period": f"{start_date} ~ {end_date}",
                "total_tasks": 0,
                "total_mileage_km": 0,
                "total_fuel_liters": 0,
                "avg_fuel_per_100km": 0,
                "error": str(e)
            }


def get_driver_task_service(db: Session) -> DriverTaskService:
    return DriverTaskService(db)
