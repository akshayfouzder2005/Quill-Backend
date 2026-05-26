import uuid
import secrets
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from app.api.rag import _push_index_job
import asyncio

from app.db.session import get_db
from app.core.deps import CurrentUser, get_document_or_404, require_role
from app.models.document import (
    Document, DocumentCollaborator, DocumentVersion, CollaboratorRole
)
from app.models.user import User
from app.schemas.document import (
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentDetail,
    CollaboratorAdd, CollaboratorUpdate, ShareLinkCreate, VersionResponse
)

router = APIRouter(prefix="/documents", tags=["documents"])

DB = Annotated[AsyncSession, Depends(get_db)]


# ── Create document ───────────────────────────────────────────────────
@router.post("", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: DocumentCreate,
    current_user: CurrentUser,
    db: DB,
):
    doc = Document(
        title=body.title,
        owner_id=current_user.id,
    )
    db.add(doc)
    await db.flush()   # get UUID before commit

    # Add owner as collaborator too (makes queries uniform)
    collab = DocumentCollaborator(
        document_id=doc.id,
        user_id=current_user.id,
        role=CollaboratorRole.OWNER,
    )
    db.add(collab)

    result = DocumentResponse.model_validate(doc)
    result.role = CollaboratorRole.OWNER
    return result


# ── List documents ────────────────────────────────────────────────────
@router.get("", response_model=list[DocumentResponse])
async def list_documents(current_user: CurrentUser, db: DB):
    # Documents where user is owner OR collaborator
    result = await db.execute(
        select(Document, DocumentCollaborator.role)
        .join(
            DocumentCollaborator,
            and_(
                DocumentCollaborator.document_id == Document.id,
                DocumentCollaborator.user_id == current_user.id,
            )
        )
        .order_by(Document.updated_at.desc())
    )
    rows = result.all()
    docs = []
    for doc, role in rows:
        r = DocumentResponse.model_validate(doc)
        r.role = role
        docs.append(r)
    return docs


# ── Get single document ───────────────────────────────────────────────
@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    # Single query: load doc + collaborators + their users
    result = await db.execute(
        select(Document)
        .options(
            selectinload(Document.collaborators)
            .selectinload(DocumentCollaborator.user)
        )
        .where(Document.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check access and get caller's role
    _, role = await get_document_or_404(document_id, db, current_user)

    # Build collaborators manually — avoids Pydantic trying to traverse ORM
    collaborators = [
        {
            "user_id":    c.user_id,
            "username":   c.user.username,
            "email":      c.user.email,
            "role":       c.role,
            "invited_at": c.invited_at,
        }
        for c in doc.collaborators
        if c.user is not None
    ]

    return DocumentDetail(
        id=doc.id,
        title=doc.title,
        owner_id=doc.owner_id,
        is_public=doc.is_public,
        share_token=doc.share_token,
        version=doc.version,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        content_text=doc.content_text,
        role=role,
        collaborators=collaborators,
    )


# ── Update document ───────────────────────────────────────────────────
@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: uuid.UUID,
    body: DocumentUpdate,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER, CollaboratorRole.EDITOR)(role)

    if body.title is not None:
        doc.title = body.title

    content_changed = False
    if body.content_text is not None:
        doc.content_text = body.content_text
        doc.version += 1
        content_changed = True

        snapshot = DocumentVersion(
            document_id=doc.id,
            version_number=doc.version,
            content_text=body.content_text,
            saved_by_id=current_user.id,
        )
        db.add(snapshot)

    r = DocumentResponse.model_validate(doc)
    r.role = role

    if content_changed:
        asyncio.create_task(_push_index_job(doc.id, body.content_text))

    return r
# ── Delete document ───────────────────────────────────────────────────
@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER)(role)
    await db.delete(doc)


# ── Share link ────────────────────────────────────────────────────────
@router.post("/{document_id}/share", response_model=DocumentResponse)
async def manage_share_link(
    document_id: uuid.UUID,
    body: ShareLinkCreate,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER)(role)

    if body.enabled:
        doc.share_token = secrets.token_urlsafe(32)
        doc.is_public = True
    else:
        doc.share_token = None
        doc.is_public = False

    r = DocumentResponse.model_validate(doc)
    r.role = role
    return r


# ── Version history ───────────────────────────────────────────────────
@router.get("/{document_id}/versions", response_model=list[VersionResponse])
async def list_versions(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    await get_document_or_404(document_id, db, current_user)

    result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    return result.scalars().all()


# ── Restore version ───────────────────────────────────────────────────
@router.post("/{document_id}/versions/{version_id}/restore",
             response_model=DocumentResponse)
async def restore_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER)(role)

    result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document_id,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    doc.content_text = version.content_text
    doc.content_binary = version.content_binary
    doc.version += 1

    # Save the restore itself as a new version
    snapshot = DocumentVersion(
        document_id=doc.id,
        version_number=doc.version,
        content_text=doc.content_text,
        saved_by_id=current_user.id,
    )
    db.add(snapshot)

    r = DocumentResponse.model_validate(doc)
    r.role = role
    return r


# ── Add collaborator ──────────────────────────────────────────────────
@router.post("/{document_id}/collaborators", status_code=201)
async def add_collaborator(
    document_id: uuid.UUID,
    body: CollaboratorAdd,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER)(role)

    # Find user by email
    user_result = await db.execute(
        select(User).where(User.email == body.email)
    )
    invitee = user_result.scalar_one_or_none()
    if not invitee:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a collaborator
    existing = await db.execute(
        select(DocumentCollaborator).where(
            DocumentCollaborator.document_id == document_id,
            DocumentCollaborator.user_id == invitee.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a collaborator")

    collab = DocumentCollaborator(
        document_id=document_id,
        user_id=invitee.id,
        role=body.role,
    )
    db.add(collab)
    return {"message": f"{invitee.username} added as {body.role.value}"}


# ── Update collaborator role ──────────────────────────────────────────
@router.patch("/{document_id}/collaborators/{user_id}")
async def update_collaborator(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    body: CollaboratorUpdate,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER)(role)

    result = await db.execute(
        select(DocumentCollaborator).where(
            DocumentCollaborator.document_id == document_id,
            DocumentCollaborator.user_id == user_id,
        )
    )
    collab = result.scalar_one_or_none()
    if not collab:
        raise HTTPException(status_code=404, detail="Collaborator not found")

    collab.role = body.role
    return {"message": f"Role updated to {body.role.value}"}


# ── Remove collaborator ───────────────────────────────────────────────
@router.delete("/{document_id}/collaborators/{user_id}", status_code=204)
async def remove_collaborator(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: DB,
):
    doc, role = await get_document_or_404(document_id, db, current_user)
    require_role(CollaboratorRole.OWNER)(role)

    result = await db.execute(
        select(DocumentCollaborator).where(
            DocumentCollaborator.document_id == document_id,
            DocumentCollaborator.user_id == user_id,
        )
    )
    collab = result.scalar_one_or_none()
    if not collab:
        raise HTTPException(status_code=404, detail="Collaborator not found")

    await db.delete(collab)