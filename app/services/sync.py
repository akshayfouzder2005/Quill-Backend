# Redis pub/sub manager
import asyncio
import logging
from typing import Dict, Set
from fastapi import WebSocket
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global connection pool — one per process
_redis: aioredis.Redis | None = None

def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=False,  # binary — Yjs updates are raw bytes
        )
    return _redis


class ConnectionManager:
    """
    Tracks all active WebSocket connections per document.
    Handles Redis pub/sub fanout across connections.
    """

    def __init__(self):
        # document_id (str) → set of active WebSocket connections
        self.rooms: Dict[str, Set[WebSocket]] = {}

    def _channel(self, document_id: str) -> str:
        return f"doc:{document_id}"

    async def connect(self, websocket: WebSocket, document_id: str):
        await websocket.accept()
        if document_id not in self.rooms:
            self.rooms[document_id] = set()
        self.rooms[document_id].add(websocket)
        logger.info(f"WS connected: doc={document_id}, total={len(self.rooms[document_id])}")

    def disconnect(self, websocket: WebSocket, document_id: str):
        room = self.rooms.get(document_id, set())
        room.discard(websocket)
        if not room:
            self.rooms.pop(document_id, None)
        logger.info(f"WS disconnected: doc={document_id}")

    async def broadcast_local(self, document_id: str, data: bytes, exclude: WebSocket):
        """Send to all local connections in this room except sender."""
        dead = set()
        for ws in self.rooms.get(document_id, set()):
            if ws is exclude:
                continue
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws, document_id)

    async def publish(self, document_id: str, data: bytes):
        """Publish update to Redis — fans out to ALL FastAPI instances."""
        r = get_redis()
        await r.publish(self._channel(document_id), data)

    async def subscribe_and_relay(self, document_id: str):
        """
        Long-running coroutine: subscribes to Redis channel and
        broadcasts received messages to local WebSocket connections.
        One per document per process.
        """
        r = get_redis()
        pubsub = r.pubsub()
        channel = self._channel(document_id)
        await pubsub.subscribe(channel)
        logger.info(f"Redis subscribed: {channel}")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data: bytes = message["data"]
                # Relay to all local connections (no exclude — came from Redis, not a local sender)
                dead = set()
                for ws in self.rooms.get(document_id, set()):
                    try:
                        await ws.send_bytes(data)
                    except Exception:
                        dead.add(ws)
                for ws in dead:
                    self.disconnect(ws, document_id)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            logger.info(f"Redis unsubscribed: {channel}")


# Singleton — shared across all WebSocket connections in this process
manager = ConnectionManager()

# Tracks running subscriber tasks per document: doc_id → asyncio.Task
_subscriber_tasks: Dict[str, asyncio.Task] = {}


async def ensure_subscribed(document_id: str):
    """Start a Redis subscriber task for this document if not already running."""
    if document_id in _subscriber_tasks:
        task = _subscriber_tasks[document_id]
        if not task.done():
            return  # already running
    task = asyncio.create_task(manager.subscribe_and_relay(document_id))
    _subscriber_tasks[document_id] = task


async def cleanup_subscriber(document_id: str):
    """Cancel subscriber task when last client leaves the document."""
    if document_id not in manager.rooms or not manager.rooms.get(document_id):
        task = _subscriber_tasks.pop(document_id, None)
        if task and not task.done():
            task.cancel()