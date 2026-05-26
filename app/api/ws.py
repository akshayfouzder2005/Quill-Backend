#  Websocket endpoint
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.core.security import decode_token
from app.models.user import User
from app.models.document import Document, DocumentCollaborator
from app.services.sync import manager, ensure_subscribed, cleanup_subscriber
import uuid

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


async def _get_user_from_token(token: str) -> User | None:
    """Validate JWT and return User. Returns None on any failure."""
    try:
        payload = decode_token(token)
    except ValueError:
        return None

    if payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            return None
        return user


async def _check_document_access(document_id: uuid.UUID, user: User) -> bool:
    """Returns True if user can access this document."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            return False

        # Owner always has access
        if doc.owner_id == user.id:
            return True

        # Check collaborator table
        collab = await db.execute(
            select(DocumentCollaborator).where(
                DocumentCollaborator.document_id == document_id,
                DocumentCollaborator.user_id == user.id,
            )
        )
        if collab.scalar_one_or_none():
            return True

        # Public docs — read access
        if doc.is_public:
            return True

        return False


@router.websocket("/ws/{document_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    document_id: uuid.UUID,
    token: str = Query(..., description="JWT access token"),
):
    # 1. Auth — can't use HTTP headers over WS handshake
    user = await _get_user_from_token(token)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 2. RBAC
    has_access = await _check_document_access(document_id, user)
    if not has_access:
        await websocket.close(code=4003, reason="Access denied")
        return

    doc_id_str = str(document_id)

    # 3. Accept + register
    await manager.connect(websocket, doc_id_str)

    # 4. Ensure Redis subscriber is running for this document
    await ensure_subscribed(doc_id_str)

    # 5. Notify room that a new user joined (presence hint)
    join_msg = f"__presence__:{user.username}:join".encode()
    await manager.publish(doc_id_str, join_msg)

    try:
        while True:
            # Receive binary Yjs update from this client
            data = await websocket.receive_bytes()

            # Skip presence messages (text-based, not Yjs binary)
            if data.startswith(b"__presence__"):
                # Relay presence as-is to room
                await manager.publish(doc_id_str, data)
                continue

            # Relay Yjs binary update to all other clients via Redis
            await manager.publish(doc_id_str, data)

    except WebSocketDisconnect:
        manager.disconnect(websocket, doc_id_str)
        leave_msg = f"__presence__:{user.username}:leave".encode()
        await manager.publish(doc_id_str, leave_msg)
        await cleanup_subscriber(doc_id_str)
        logger.info(f"User {user.username} left doc {doc_id_str}")