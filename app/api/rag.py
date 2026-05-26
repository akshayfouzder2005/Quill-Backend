import uuid
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.db.session import get_db
from app.core.deps import CurrentUser, get_document_or_404
from app.services.rag import retrieve_chunks, build_prompt, index_document, client, CHAT_MODEL
from app.services.sync import get_redis
from app.models.document import CollaboratorRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["rag"])

STREAM_KEY = "quill:index_jobs"


class ChatRequest(BaseModel):
    question: str


async def _push_index_job(document_id: uuid.UUID, content_text: str):
    """Push indexing job to Redis Streams."""
    r = get_redis()
    await r.xadd(STREAM_KEY, {
        "document_id": str(document_id),
        "content_text": content_text,
    })


@router.post("/{document_id}/chat")
async def chat(
    document_id: uuid.UUID,
    body: ChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Stream a RAG-grounded answer via SSE."""
    # RBAC — all roles can chat
    await get_document_or_404(document_id, db, current_user)

    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # Retrieve relevant chunks
    chunks = await retrieve_chunks(document_id, body.question, db)

    if not chunks:
        async def empty_stream():
            yield "data: " + json.dumps({"token": "I couldn't find that in this document."}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    # Build prompt
    messages = build_prompt(chunks, body.question)

    # Stream GPT-4o-mini response
    async def token_stream():
        try:
            stream = await client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                max_tokens=1000,
                temperature=0.2,  # low temp for factual grounded answers
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield "data: " + json.dumps({"token": delta}) + "\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"Chat stream error: {e}")
            yield "data: " + json.dumps({"error": str(e)}) + "\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        token_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables Nginx buffering on Render
        },
    )


@router.post("/{document_id}/reindex", status_code=202)
async def reindex(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger re-embedding of a document. Owner/editor only."""
    from app.models.document import Document
    from sqlalchemy import select

    doc, role = await get_document_or_404(document_id, db, current_user)

    if role not in (CollaboratorRole.OWNER, CollaboratorRole.EDITOR):
        raise HTTPException(status_code=403, detail="Editor or owner required")

    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    doc_obj = result.scalar_one_or_none()

    if not doc_obj or not doc_obj.content_text:
        raise HTTPException(status_code=400, detail="Document has no text content to index")

    await _push_index_job(document_id, doc_obj.content_text)
    return {"message": "Reindex job queued", "document_id": str(document_id)}