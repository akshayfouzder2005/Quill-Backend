import tiktoken
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pgvector.sqlalchemy import Vector
from sqlalchemy import func
from app.core.config import settings
from app.models.chunk import DocumentChunk
import uuid

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
CHUNK_TOKENS = 300
OVERLAP_TOKENS = 50
TOP_K = 5


def _tokenizer():
    return tiktoken.encoding_for_model("text-embedding-3-small")


def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of ~300 tokens."""
    enc = _tokenizer()
    tokens = enc.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = start + CHUNK_TOKENS
        chunk_tokens = tokens[start:end]
        chunk_text = enc.decode(chunk_tokens)
        chunks.append(chunk_text)
        if end >= len(tokens):
            break
        start += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings, returns list of 1536-dim vectors."""
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


async def index_document(
    document_id: uuid.UUID,
    content_text: str,
    db: AsyncSession,
):
    """
    Chunk → embed → upsert into document_chunks.
    Deletes old chunks for this document first.
    """
    if not content_text or not content_text.strip():
        return

    # Delete old chunks
    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )

    # Chunk the text
    chunks = chunk_text(content_text)
    if not chunks:
        return

    # Embed all chunks in one API call (efficient)
    embeddings = await embed_texts(chunks)

    # Insert new chunks
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        db.add(DocumentChunk(
            document_id=document_id,
            chunk_index=i,
            content=chunk,
            embedding=embedding,
        ))

    await db.flush()


async def retrieve_chunks(
    document_id: uuid.UUID,
    question: str,
    db: AsyncSession,
) -> list[DocumentChunk]:
    """Embed question → cosine similarity search → return top-K chunks."""
    question_embedding = (await embed_texts([question]))[0]

    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.embedding.cosine_distance(question_embedding))
        .limit(TOP_K)
    )
    return result.scalars().all()


def build_prompt(chunks: list[DocumentChunk], question: str) -> list[dict]:
    """Build the messages array for GPT-4o-mini."""
    context_parts = []
    for i, chunk in enumerate(chunks):
        context_parts.append(f"[chunk {i + 1}]\n{chunk.content}")

    context = "\n---\n".join(context_parts)

    system_prompt = (
        "You are a document assistant for Quill, a collaborative editor.\n"
        "Answer ONLY using the context below.\n"
        "If the answer is not in the context, say: "
        "'I couldn't find that in this document.'\n"
        "Cite chunk numbers in your answer, e.g. [chunk 1].\n\n"
        f"Context:\n{context}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]