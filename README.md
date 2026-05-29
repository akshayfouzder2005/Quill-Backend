<div align="center">

<br/>

```
 ██████╗ ██╗   ██╗██╗██╗     ██╗
██╔═══██╗██║   ██║██║██║     ██║
██║   ██║██║   ██║██║██║     ██║
██║▄▄ ██║██║   ██║██║██║     ██║
╚██████╔╝╚██████╔╝██║███████╗███████╗
 ╚══▀▀═╝  ╚═════╝ ╚═╝╚══════╝╚══════╝
```

**Real-time Collaborative Editor — Backend API**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![pgvector](https://img.shields.io/badge/pgvector-enabled-blueviolet?style=flat-square)](https://github.com/pgvector/pgvector)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)

<br/>

*Quill is a production-ready backend for a collaborative document editor — powered by WebSockets, Yjs CRDT sync, Redis Pub/Sub, and RAG-based AI chat over your documents.*

<br/>

</div>

---

## ✨ Features

- **🔄 Real-time Collaboration** — WebSocket-based document sync using [Yjs](https://yjs.dev/) binary CRDT updates, with Redis Pub/Sub fan-out across horizontally-scaled server instances
- **🤖 AI Document Chat (RAG)** — Ask questions about any document and get streamed, context-grounded answers via OpenAI `gpt-4o-mini` and `text-embedding-3-small`
- **🧠 Vector Search** — Document chunks embedded with 1536-dim vectors and stored in PostgreSQL via `pgvector`, queried by cosine similarity
- **⚙️ Background Indexer** — Redis Streams consumer (`xreadgroup`) that chunks, embeds, and upserts document content asynchronously
- **🔐 JWT Auth** — Access + refresh token flow with `python-jose`, bcrypt password hashing, and per-document RBAC (owner / editor / commenter / viewer)
- **📜 Version History** — Automatic document versioning with full Yjs binary snapshots
- **💬 Comments** — Thread-based commenting system with resolve/expire support
- **🌐 Share Links** — Public documents and token-based sharing
- **🐳 Docker-first** — One-command local environment with `docker-compose`

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI App                             │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │   Auth   │  │  Docs    │  │ WebSocket│  │  RAG / Chat  │   │
│  │  /auth   │  │ /docs    │  │ /ws/{id} │  │  /documents  │   │
│  └──────────┘  └──────────┘  └────┬─────┘  └──────┬───────┘   │
│                                   │               │            │
└───────────────────────────────────┼───────────────┼────────────┘
                                    │               │
                    ┌───────────────▼──┐    ┌───────▼──────────┐
                    │   Redis Pub/Sub  │    │  Indexer Worker   │
                    │  (Yjs fan-out)   │    │ (Redis Streams)   │
                    └───────────────┬──┘    └───────┬──────────┘
                                    │               │
                    ┌───────────────▼───────────────▼──────────┐
                    │         PostgreSQL + pgvector             │
                    │  users · documents · chunks · versions    │
                    └──────────────────────────────────────────┘
```

### Key design decisions

- **Yjs over WebSocket + Redis** — Clients exchange raw binary Yjs updates. The server relays them through a Redis channel (`doc:<id>`), so multiple Uvicorn workers all stay in sync without sticky sessions.
- **Redis Streams for indexing** — When a document is saved, an indexing job is pushed to `quill:index_jobs`. The background `run_indexer` coroutine consumes jobs via consumer groups, ensuring exactly-once processing with automatic retry on failure.
- **Async throughout** — `asyncpg` + `SQLAlchemy 2.0` async sessions, `redis.asyncio`, and `openai` async client — the whole stack is non-blocking.

---

## 🗂️ Project Structure

```
Quill-Backend/
├── app/
│   ├── api/
│   │   ├── auth.py          # Register, login, refresh, /me
│   │   ├── documents.py     # CRUD, share tokens, collaborators, versions
│   │   ├── ws.py            # WebSocket endpoint with RBAC
│   │   └── rag.py           # /chat (SSE) and /reindex endpoints
│   ├── core/
│   │   ├── config.py        # Pydantic-settings environment config
│   │   ├── security.py      # JWT creation/decoding, bcrypt hashing
│   │   └── deps.py          # FastAPI dependencies (CurrentUser, get_document_or_404)
│   ├── db/
│   │   ├── session.py       # AsyncSessionLocal, get_db dependency
│   │   └── base.py          # SQLAlchemy declarative Base
│   ├── models/
│   │   ├── user.py          # User model
│   │   ├── document.py      # Document, DocumentCollaborator, DocumentVersion, Comment
│   │   └── chunk.py         # DocumentChunk (pgvector embedding column)
│   ├── schemas/
│   │   ├── auth.py          # Register/Login/Token/User Pydantic schemas
│   │   └── document.py      # Document read/write schemas
│   ├── services/
│   │   ├── sync.py          # ConnectionManager (WebSocket rooms + Redis pub/sub)
│   │   └── rag.py           # chunk_text, embed_texts, index_document, retrieve_chunks
│   ├── workers/
│   │   └── indexer.py       # Redis Streams consumer (run_indexer coroutine)
│   └── main.py              # App factory, middleware, router registration
├── alembic/                 # Database migrations
│   └── versions/
├── docker-compose.yml       # PostgreSQL (pgvector) + Redis
├── requirements.txt
└── .env.example
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.13+
- Docker & Docker Compose
- An [OpenAI API key](https://platform.openai.com/api-keys)

### 1. Clone & install dependencies

```bash
git clone https://github.com/your-username/Quill-Backend.git
cd Quill-Backend

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
# Database
DATABASE_URL=postgresql+asyncpg://quill:quill@localhost:5432/quill_db

# Redis
REDIS_URL=redis://localhost:6379/0

# Auth — generate with: openssl rand -hex 32
SECRET_KEY=your_secure_secret_here
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# OpenAI (required for RAG/chat features)
OPENAI_API_KEY=sk-...

# App
APP_ENV=development
CORS_ORIGINS=http://localhost:5173
```

### 3. Start infrastructure

```bash
docker-compose up -d
```

This starts PostgreSQL 16 with pgvector and Redis 7.

### 4. Run database migrations

```bash
alembic upgrade head
```

### 5. Start the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now live at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## 📡 API Reference

### Authentication — `/api/v1/auth`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/register` | Create account, returns access + refresh tokens |
| `POST` | `/login` | Login, returns access + refresh tokens |
| `POST` | `/refresh` | Exchange refresh token for new token pair |
| `GET` | `/me` | Get current user profile |

### Documents — `/api/v1/documents`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | List all documents owned by or shared with the user |
| `POST` | `/` | Create a new document |
| `GET` | `/{id}` | Get document by ID |
| `PATCH` | `/{id}` | Update document title / content / visibility |
| `DELETE` | `/{id}` | Delete document (owner only) |
| `POST` | `/{id}/collaborators` | Invite a collaborator with a role |
| `DELETE` | `/{id}/collaborators/{user_id}` | Remove a collaborator |
| `GET` | `/{id}/versions` | List version history |
| `GET` | `/{id}/comments` | List comments on a document |
| `POST` | `/{id}/comments` | Add a comment |
| `PATCH` | `/{id}/comments/{comment_id}` | Resolve / update a comment |

### WebSocket — `/api/v1/ws/{document_id}?token=<JWT>`

Real-time collaboration endpoint. Connects a client to a document room.

- Authenticate via the `token` query parameter (JWT access token).
- Send and receive **raw Yjs binary updates** as WebSocket binary frames.
- Presence messages use the prefix `__presence__:<username>:join/leave`.

```js
// Example client connection
const ws = new WebSocket(
  `ws://localhost:8000/api/v1/ws/${documentId}?token=${accessToken}`
);
ws.binaryType = "arraybuffer";

ws.onmessage = (e) => {
  // Apply incoming Yjs update
  Y.applyUpdate(ydoc, new Uint8Array(e.data));
};

ydoc.on("update", (update) => {
  ws.send(update);  // Broadcast local change
});
```

### RAG / AI Chat — `/api/v1/documents`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/{id}/chat` | Ask a question — streams SSE tokens grounded in document content |
| `POST` | `/{id}/reindex` | Manually re-embed a document (owner/editor only) |

**Chat response format (Server-Sent Events):**

```
data: {"token": "The"}
data: {"token": " document"}
data: {"token": " mentions..."}
data: [DONE]
```

---

## 🔒 Role-Based Access Control

| Role | Read | Edit | Comment | Manage Collaborators | Delete |
|------|------|------|---------|---------------------|--------|
| `owner` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `editor` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `commenter` | ✅ | ❌ | ✅ | ❌ | ❌ |
| `viewer` | ✅ | ❌ | ❌ | ❌ | ❌ |

Public documents are readable without authentication. WebSocket connections are validated for RBAC before the handshake is accepted.

---

## 🧬 RAG Pipeline

```
Document saved
      │
      ▼
 Push to Redis Stream ──► run_indexer (background coroutine)
 (quill:index_jobs)              │
                                 ▼
                        chunk_text() — 300 token chunks
                        with 50 token overlap (tiktoken)
                                 │
                                 ▼
                        embed_texts() — OpenAI text-embedding-3-small
                        1536-dim vectors
                                 │
                                 ▼
                        Upsert into document_chunks (pgvector)

User sends /chat question
      │
      ▼
 Embed question ──► cosine similarity search (top-5 chunks)
      │
      ▼
 Build prompt with context chunks
      │
      ▼
 Stream gpt-4o-mini response via SSE
```

---

## 🧪 Running Tests

```bash
pytest tests/ -v
```

> Tests require a running PostgreSQL and Redis instance (use `docker-compose up -d`).

---

## 🐳 Docker Deployment

For production, build and run the app container alongside the compose services:

```bash
# Build image
docker build -t quill-backend .

# Run with compose services
docker-compose up -d
docker run --env-file .env --network host quill-backend
```

For multi-worker deployments (important for WebSocket horizontal scaling), ensure `REDIS_URL` points to the same Redis instance across all workers — the pub/sub fan-out handles cross-worker message delivery automatically.

---

## ⚙️ Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | ✅ | — | Async PostgreSQL URL (`postgresql+asyncpg://...`) |
| `REDIS_URL` | ✅ | — | Redis connection URL |
| `SECRET_KEY` | ✅ | — | JWT signing secret (min 32 chars recommended) |
| `OPENAI_API_KEY` | ✅ | — | OpenAI API key for embeddings and chat |
| `ALGORITHM` | ❌ | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | ❌ | `60` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | ❌ | `7` | Refresh token lifetime |
| `APP_ENV` | ❌ | `development` | Environment flag |
| `CORS_ORIGINS` | ❌ | `http://localhost:5173` | Comma-separated allowed origins |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI + Uvicorn (ASGI) |
| Database | PostgreSQL 16 + pgvector |
| ORM | SQLAlchemy 2.0 (async) + Alembic |
| Cache / Pub-Sub | Redis 7 (asyncio client) |
| Real-time Sync | WebSockets + Yjs CRDT binary protocol |
| AI / Embeddings | OpenAI `text-embedding-3-small` + `gpt-4o-mini` |
| Auth | JWT (`python-jose`) + bcrypt (`passlib`) |
| Validation | Pydantic v2 + pydantic-settings |
| Migrations | Alembic |
| Containerization | Docker + Docker Compose |

---

## 🤝 Contributing

Contributions are welcome! Please open an issue to discuss your idea before submitting a PR.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more information.

---

<div align="center">

Built with ❤️ using FastAPI, PostgreSQL, Redis, and OpenAI

</div>