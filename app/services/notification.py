from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models import Notification, Employee, Driver


class NotificationService:
    def __init__(self):
        self._ws_connections = {}

    def create_notification(
        self, db: Session, type: str,
        employee_id: Optional[int] = None,
        driver_id: Optional[int] = None,
        title: str = "",
        content: str = "",
        related_id: Optional[int] = None,
        related_type: Optional[str] = None
    ) -> Notification:
        notification = Notification(
            type=type,
            employee_id=employee_id,
            driver_id=driver_id,
            title=title,
            content=content,
            related_id=related_id,
            related_type=related_type,
            is_read=False,
            created_at=datetime.utcnow()
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)
        return notification

    def broadcast_notification(self, notification: Notification):
        pass

    def get_employee_notifications(
        self, db: Session, employee_id: int, unread_only: bool = False
    ) -> List[Notification]:
        query = db.query(Notification).filter(Notification.employee_id == employee_id)
        if unread_only:
            query = query.filter(Notification.is_read == False)
        return query.order_by(Notification.created_at.desc()).all()

    def get_driver_notifications(
        self, db: Session, driver_id: int, unread_only: bool = False
    ) -> List[Notification]:
        query = db.query(Notification).filter(Notification.driver_id == driver_id)
        if unread_only:
            query = query.filter(Notification.is_read == False)
        return query.order_by(Notification.created_at.desc()).all()

    def mark_as_read(self, db: Session, notification_id: int) -> bool:
        notification = db.query(Notification).filter(Notification.id == notification_id).first()
        if notification:
            notification.is_read = True
            db.commit()
            return True
        return False

    def mark_all_as_read(self, db: Session, user_type: str, user_id: int) -> int:
        query = db.query(Notification)
        if user_type == "employee":
            query = query.filter(Notification.employee_id == user_id)
        elif user_type == "driver":
            query = query.filter(Notification.driver_id == user_id)
        else:
            return 0
        notifications = query.filter(Notification.is_read == False).all()
        for n in notifications:
            n.is_read = True
        db.commit()
        return len(notifications)


notification_service = NotificationService()
