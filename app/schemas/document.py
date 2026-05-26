import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional
from app.models.document import CollaboratorRole


class DocumentCreate(BaseModel):
    title: str = Field(default="Untitled document", max_length=500)


class DocumentUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    content_text: Optional[str] = None


class CollaboratorAdd(BaseModel):
    email: str
    role: CollaboratorRole = CollaboratorRole.VIEWER


class CollaboratorUpdate(BaseModel):
    role: CollaboratorRole


class ShareLinkCreate(BaseModel):
    enabled: bool = True   # False = revoke the share link


# ── Response shapes ───────────────────────────────────────────────────

class CollaboratorResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    email: str
    role: CollaboratorRole
    invited_at: datetime

    model_config = {"from_attributes": True}



class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    owner_id: uuid.UUID
    is_public: bool
    share_token: Optional[str]
    version: int
    created_at: datetime
    updated_at: datetime
    role: Optional[CollaboratorRole] = None   # caller's role on this doc

    model_config = {"from_attributes": True}


class DocumentDetail(DocumentResponse):
    content_text: Optional[str]
    collaborators: list[CollaboratorResponse] = []


class VersionResponse(BaseModel):
    id: uuid.UUID
    version_number: int
    saved_by_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}