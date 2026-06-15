from typing import Optional, List
from datetime import datetime
import asyncio
import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models import Notification, Employee, Driver

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self):
        self._ws_connections = {}
        self._event_loop = None

    def set_event_loop(self, loop):
        self._event_loop = loop

    def _run_async(self, coro):
        try:
            if self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self._event_loop)
            else:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(coro)
                finally:
                    loop.close()
        except Exception as e:
            logger.warning(f"异步推送执行失败: {e}")

    def create_notification(
        self, db: Session, type: str,
        employee_id: Optional[int] = None,
        driver_id: Optional[int] = None,
        title: str = "",
        content: str = "",
        related_id: Optional[int] = None,
        related_type: Optional[str] = None,
        extra: Optional[dict] = None
    ) -> Optional[Notification]:
        try:
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

            try:
                self._push_via_websocket(notification, extra)
            except Exception as e:
                logger.warning(f"WebSocket推送失败，不影响主流程: {e}")

            return notification
        except Exception as e:
            logger.error(f"创建通知失败: {e}", exc_info=True)
            try:
                db.rollback()
            except:
                pass
            return None

    def _push_via_websocket(self, notification: Notification, extra: Optional[dict] = None):
        from app.services.websocket import notify_employee, notify_driver, manager

        message = manager.build_message(
            msg_type=notification.type,
            title=notification.title,
            content=notification.content,
            related_id=notification.related_id,
            related_type=notification.related_type,
            extra=extra
        )
        message["notification_id"] = notification.id
        message["is_read"] = False

        if notification.employee_id:
            self._run_async(notify_employee(
                notification.employee_id,
                notification.type,
                notification.title,
                notification.content,
                notification.related_id,
                notification.related_type,
                {**(extra or {}), "notification_id": notification.id}
            ))

        if notification.driver_id:
            self._run_async(notify_driver(
                notification.driver_id,
                notification.type,
                notification.title,
                notification.content,
                notification.related_id,
                notification.related_type,
                {**(extra or {}), "notification_id": notification.id}
            ))

    def broadcast_notification(self, notification: Notification):
        pass

    def get_employee_notifications(
        self, db: Session, employee_id: int, unread_only: bool = False
    ) -> List[Notification]:
        try:
            query = db.query(Notification).filter(Notification.employee_id == employee_id)
            if unread_only:
                query = query.filter(Notification.is_read == False)
            return query.order_by(Notification.created_at.desc()).all()
        except Exception as e:
            logger.error(f"查询员工通知失败: {e}")
            return []

    def get_driver_notifications(
        self, db: Session, driver_id: int, unread_only: bool = False
    ) -> List[Notification]:
        try:
            query = db.query(Notification).filter(Notification.driver_id == driver_id)
            if unread_only:
                query = query.filter(Notification.is_read == False)
            return query.order_by(Notification.created_at.desc()).all()
        except Exception as e:
            logger.error(f"查询司机通知失败: {e}")
            return []

    def mark_as_read(self, db: Session, notification_id: int) -> bool:
        try:
            notification = db.query(Notification).filter(Notification.id == notification_id).first()
            if notification:
                notification.is_read = True
                db.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"标记通知已读失败: {e}")
            try:
                db.rollback()
            except:
                pass
            return False

    def mark_all_as_read(self, db: Session, user_type: str, user_id: int) -> int:
        try:
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
        except Exception as e:
            logger.error(f"批量标记已读失败: {e}")
            try:
                db.rollback()
            except:
                pass
            return 0


notification_service = NotificationService()
