# Redis Streams consumer:
import asyncio
import json
import logging
import uuid
from app.services.rag import index_document
from app.db.session import AsyncSessionLocal
from app.services.sync import get_redis

logger = logging.getLogger(__name__)

STREAM_KEY = "quill:index_jobs"
CONSUMER_GROUP = "indexer_group"
CONSUMER_NAME = "worker_1"


async def _ensure_group():
    """Create consumer group if it doesn't exist."""
    r = get_redis()
    try:
        await r.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info(f"Created consumer group: {CONSUMER_GROUP}")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            pass  # group already exists, fine
        else:
            logger.error(f"Redis group create error: {e}")


async def run_indexer():
    """
    Long-running Redis Streams consumer.
    Picks up index jobs and runs chunk → embed → upsert.
    """
    await _ensure_group()
    r = get_redis()
    logger.info("Indexer workers started, waiting for jobs...")

    while True:
        try:
            # Block for up to 5s waiting for new messages
            messages = await r.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM_KEY: ">"},
                count=1,
                block=5000,
            )

            if not messages:
                continue

            for stream, entries in messages:
                for entry_id, fields in entries:
                    try:
                        document_id = uuid.UUID(fields[b"document_id"].decode())
                        content_text = fields[b"content_text"].decode()

                        logger.info(f"Indexing document: {document_id}")

                        async with AsyncSessionLocal() as db:
                            await index_document(document_id, content_text, db)
                            await db.commit()

                        # Acknowledge message
                        await r.xack(STREAM_KEY, CONSUMER_GROUP, entry_id)
                        logger.info(f"Indexed document: {document_id}")

                    except Exception as e:
                        logger.error(f"Indexer error for entry {entry_id}: {e}")
                        # Don't ack — message stays for retry

        except asyncio.CancelledError:
            logger.info("Indexer workers cancelled")
            break
        except Exception as e:
            logger.error(f"Indexer loop error: {e}")
            await asyncio.sleep(2)  # backoff before retry