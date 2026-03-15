import json
from fastapi import WebSocket

class WebSocketManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self._connections[user_id] = ws

    def disconnect(self, user_id: str):
        self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: str, data: dict):
        ws = self._connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                self.disconnect(user_id)
