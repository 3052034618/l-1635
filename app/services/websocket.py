import json
from typing import Dict, Set, Optional
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session


class ConnectionManager:
    def __init__(self):
        self._employee_connections: Dict[int, Set[WebSocket]] = {}
        self._driver_connections: Dict[int, Set[WebSocket]] = {}
        self._active_connections: Set[WebSocket] = set()

    async def connect_employee(self, websocket: WebSocket, employee_id: int):
        await websocket.accept()
        self._active_connections.add(websocket)
        if employee_id not in self._employee_connections:
            self._employee_connections[employee_id] = set()
        self._employee_connections[employee_id].add(websocket)

    async def connect_driver(self, websocket: WebSocket, driver_id: int):
        await websocket.accept()
        self._active_connections.add(websocket)
        if driver_id not in self._driver_connections:
            self._driver_connections[driver_id] = set()
        self._driver_connections[driver_id].add(websocket)

    def disconnect(self, websocket: WebSocket):
        self._active_connections.discard(websocket)
        for connections in self._employee_connections.values():
            connections.discard(websocket)
        for connections in self._driver_connections.values():
            connections.discard(websocket)

    async def send_to_employee(self, employee_id: int, message: dict):
        if employee_id in self._employee_connections:
            for connection in list(self._employee_connections[employee_id]):
                try:
                    await connection.send_json(message)
                except:
                    self.disconnect(connection)

    async def send_to_driver(self, driver_id: int, message: dict):
        if driver_id in self._driver_connections:
            for connection in list(self._driver_connections[driver_id]):
                try:
                    await connection.send_json(message)
                except:
                    self.disconnect(connection)

    async def broadcast(self, message: dict):
        for connection in list(self._active_connections):
            try:
                await connection.send_json(message)
            except:
                self.disconnect(connection)

    def build_message(
        self, msg_type: str, title: str, content: str,
        related_id: Optional[int] = None, related_type: Optional[str] = None,
        extra: Optional[dict] = None
    ) -> dict:
        message = {
            "type": msg_type,
            "title": title,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        }
        if related_id is not None:
            message["related_id"] = related_id
        if related_type is not None:
            message["related_type"] = related_type
        if extra:
            message.update(extra)
        return message


manager = ConnectionManager()


async def notify_employee(
    employee_id: int, msg_type: str, title: str, content: str,
    related_id: Optional[int] = None, related_type: Optional[str] = None,
    extra: Optional[dict] = None
):
    message = manager.build_message(msg_type, title, content, related_id, related_type, extra)
    await manager.send_to_employee(employee_id, message)


async def notify_driver(
    driver_id: int, msg_type: str, title: str, content: str,
    related_id: Optional[int] = None, related_type: Optional[str] = None,
    extra: Optional[dict] = None
):
    message = manager.build_message(msg_type, title, content, related_id, related_type, extra)
    await manager.send_to_driver(driver_id, message)


async def notify_all(
    msg_type: str, title: str, content: str,
    related_id: Optional[int] = None, related_type: Optional[str] = None,
    extra: Optional[dict] = None
):
    message = manager.build_message(msg_type, title, content, related_id, related_type, extra)
    await manager.broadcast(message)
