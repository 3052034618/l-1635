from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import Notification as NotificationSchema
from app.services.notification import notification_service

router = APIRouter(prefix="/notifications", tags=["通知管理"])


@router.get("/employee/{employee_id}", response_model=List[NotificationSchema], summary="获取员工通知")
def get_employee_notifications(
    employee_id: int,
    unread_only: bool = False,
    db: Session = Depends(get_db)
):
    return notification_service.get_employee_notifications(db, employee_id, unread_only)


@router.get("/driver/{driver_id}", response_model=List[NotificationSchema], summary="获取司机通知")
def get_driver_notifications(
    driver_id: int,
    unread_only: bool = False,
    db: Session = Depends(get_db)
):
    return notification_service.get_driver_notifications(db, driver_id, unread_only)


@router.post("/{notification_id}/read", summary="标记通知已读")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db)):
    success = notification_service.mark_as_read(db, notification_id)
    return {"success": success}


@router.post("/employee/{employee_id}/read-all", summary="标记员工所有通知已读")
def mark_all_employee_read(employee_id: int, db: Session = Depends(get_db)):
    count = notification_service.mark_all_as_read(db, "employee", employee_id)
    return {"marked_count": count}


@router.post("/driver/{driver_id}/read-all", summary="标记司机所有通知已读")
def mark_all_driver_read(driver_id: int, db: Session = Depends(get_db)):
    count = notification_service.mark_all_as_read(db, "driver", driver_id)
    return {"marked_count": count}
