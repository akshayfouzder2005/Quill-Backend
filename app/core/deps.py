import uuid
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.core.security import decode_token
from app.models.user import User
from app.models.document import Document, DocumentCollaborator, CollaboratorRole
from sqlalchemy import select as sa_select


bearer_scheme = HTTPBearer()

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user

async def get_document_or_404(
    document_id: uuid.UUID,
    db: AsyncSession,
    current_user: "User",
) -> tuple[Document, CollaboratorRole]:
    """
    Returns (document, caller_role) or raises 404/403.
    Checks ownership first, then collaborator table.
    """
    # Fetch document
    result = await db.execute(
        sa_select(Document).where(Document.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Owner always has owner role
    if doc.owner_id == current_user.id:
        return doc, CollaboratorRole.OWNER

    # Check collaborator table
    collab_result = await db.execute(
        sa_select(DocumentCollaborator).where(
            DocumentCollaborator.document_id == document_id,
            DocumentCollaborator.user_id == current_user.id,
        )
    )
    collab = collab_result.scalar_one_or_none()
    if collab:
        return doc, collab.role

    # Public documents are readable by anyone
    if doc.is_public:
        return doc, CollaboratorRole.VIEWER

    raise HTTPException(status_code=403, detail="Access denied")


def require_role(*allowed_roles: CollaboratorRole):
    """
    Decorator-style checker. Call after get_document_or_404.
    Usage: require_role(CollaboratorRole.OWNER, CollaboratorRole.EDITOR)
    """
    def check(role: CollaboratorRole):
        if role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {[r.value for r in allowed_roles]}"
            )
    return check

# Shorthand type alias — use this in route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]