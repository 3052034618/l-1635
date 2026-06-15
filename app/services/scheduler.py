from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import logging

from app.database import SessionLocal
from app.services.reservation import ReservationService
from app.services.schedule_management import ScheduleManagementService
from app.services.driver_task import DriverTaskService
from app.services.performance import PerformanceReportService
from app.config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def cleanup_expired_seat_locks():
    db = SessionLocal()
    try:
        service = ReservationService(db)
        count = service.cleanup_expired_locks()
        if count > 0:
            logger.info(f"清理过期座位锁定: {count} 个")
    except Exception as e:
        logger.error(f"清理座位锁定失败: {e}")
    finally:
        db.close()


def check_upcoming_schedules():
    db = SessionLocal()
    try:
        service = ScheduleManagementService(db)
        results = service.check_upcoming_schedules(settings.NOTIFY_BEFORE_DEPARTURE)
        cancelled = sum(1 for r in results if r.get("cancelled"))
        if cancelled > 0:
            logger.info(f"检查即将发车间次: 取消 {cancelled} 个班次")
    except Exception as e:
        logger.error(f"检查班次失败: {e}")
    finally:
        db.close()


def assign_today_tasks():
    db = SessionLocal()
    try:
        service = DriverTaskService(db)
        result = service.auto_assign_all_drivers()
        logger.info(f"分配当日任务: 共 {result['tasks_created']} 个任务")
    except Exception as e:
        logger.error(f"分配任务失败: {e}")
    finally:
        db.close()


def generate_monthly_performance_report():
    now = datetime.now()
    db = SessionLocal()
    try:
        service = PerformanceReportService(db)
        reports = service.generate_monthly_report(now.year, now.month)
        logger.info(f"生成月度绩效报表: 共 {len(reports)} 条线路数据")
    except Exception as e:
        logger.error(f"生成绩效报表失败: {e}")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(
        cleanup_expired_seat_locks,
        trigger=IntervalTrigger(minutes=5),
        id="cleanup_seat_locks",
        replace_existing=True
    )

    scheduler.add_job(
        check_upcoming_schedules,
        trigger=IntervalTrigger(minutes=5),
        id="check_upcoming_schedules",
        replace_existing=True
    )

    scheduler.add_job(
        assign_today_tasks,
        trigger=CronTrigger(hour=6, minute=0),
        id="assign_daily_tasks",
        replace_existing=True
    )

    scheduler.add_job(
        generate_monthly_performance_report,
        trigger=CronTrigger(day=1, hour=1, minute=0),
        id="generate_monthly_report",
        replace_existing=True
    )

    scheduler.start()
    logger.info("定时任务调度器已启动")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("定时任务调度器已停止")
