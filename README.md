# Watch Picker API

FastAPI backend for a collaborative "what should we watch" experience. Users can befriend each other, form groups, build a watchlist, and run time-boxed "tonight" sessions where members vote on candidates. The app optionally uses OpenAI to parse free-text constraints and rerank candidates.

## Quick start

1) Install dependencies (includes OAuth support)
```bash
pip install authlib itsdangerous
```

2) Start Postgres
```bash
docker compose up -d
```

3) Create `.env` (example)
```bash
ENV=local
DATABASE_URL=postgresql+asyncpg://watchpicker:watchpicker@localhost:5432/watchpicker
JWT_SECRET=dev-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200
CORS_ORIGINS=http://localhost:5173
TMDB_TOKEN=your_tmdb_bearer_token
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-5-mini
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_FACEBOOK_CLIENT_ID=
OAUTH_FACEBOOK_CLIENT_SECRET=
OAUTH_GOOGLE_CALLBACK_URL=http://localhost:8000/auth/google/callback
OAUTH_FACEBOOK_CALLBACK_URL=http://localhost:8000/auth/facebook/callback
OAUTH_FRONTEND_SUCCESS_URL=http://localhost:5173/app
OAUTH_FRONTEND_FAILURE_URL=http://localhost:5173/login
OAUTH_SESSION_SECRET=
```

4) Run migrations
```bash
alembic upgrade head
```

5) Run the API
```bash
make api
```

API will start on `http://127.0.0.1:8000`.

## Architecture overview

- FastAPI app: `app/main.py`
- Async SQLAlchemy: `app/db/session.py`
- Alembic migrations: `alembic/`
- Service layer (business logic): `app/services/`
- API routers: `app/api/routes/`
- Pydantic schemas: `app/schemas/`
- Models: `app/models/`

The app uses cookie-based auth. `POST /auth/login` sets an `access_token` cookie which is required for all protected routes. Most route handlers delegate to a service module to enforce permissions and implement domain logic.

## Directory layout

- `app/main.py`: FastAPI setup, CORS, router registration, MCP mount.
- `app/api/routes/`: route handlers (auth, friends, groups, watchlist, sessions, tmdb, health).
- `app/services/`: core logic (watchlist, sessions, friends, groups, AI).
- `app/models/`: SQLAlchemy models and relationships.
- `app/schemas/`: Pydantic request/response schemas.
- `app/core/config.py`: environment configuration.
- `app/core/security.py`: password hashing + JWT creation.
- `alembic/`: migrations.
- `tests/`: async integration tests.

## Data model (high level)

- users
  - Auth identity and profile fields.
- friendships
  - Unique user pairs. Required for inviting into groups.
- groups
  - Owned by a user; membership stored in group_memberships.
- group_memberships
  - Many-to-many group membership.
- group_invites
  - One-time codes to join groups.
- titles
  - Canonical media entry (tmdb or manual).
- watchlist_items
  - Group-scoped items pointing to titles.
- tonight_sessions
  - A "session" with constraints, timing, and AI metadata.
- tonight_session_candidates
  - Frozen deck for a session, ordered.
- tonight_votes
  - One vote per user per session, yes/no.

## Core flows

### Auth

- `POST /auth/register`
  - Creates a user. Rejects duplicate email/username.
- `POST /auth/login`
  - Sets a signed JWT as `access_token` cookie.
- `GET /auth/google/login` and `GET /auth/facebook/login`
  - Starts OAuth redirect flow for social sign-in.
- `GET /auth/google/callback` and `GET /auth/facebook/callback`
  - Handles provider callback, creates/links a user, sets `access_token` cookie, redirects to frontend.
- `POST /auth/logout`
  - Clears cookie.
- `GET /me`
  - Returns current user.

Auth cookie is required for all protected routes (`/friends`, `/groups`, `/watchlist`, `/sessions`).

### Friends

Friendship is required before inviting someone to a group.

1) User A generates invite: `POST /friends/invite`
2) User B accepts code: `POST /friends/accept`
3) Both can list friends: `GET /friends`

Invite codes are one-time use and expire after TTL.

### Groups

Groups are owned by a user and have members. Creation requires all added members to already be friends with the owner.

- `POST /groups`
  - Create group and set memberships (owner + invited friend IDs).
- `GET /groups`
  - List all groups the user belongs to.
- `GET /groups/{group_id}`
  - Detail with member list.
- `POST /groups/{group_id}/invite`
  - Owner-only invite code.
- `POST /groups/accept-invite`
  - Join group by code.

### Watchlist

Watchlist items belong to a group and point to a `Title`.

- `POST /groups/{group_id}/watchlist`
  - Add TMDB or manual title. TMDB adds/upserts canonical titles.
- `GET /groups/{group_id}/watchlist`
  - List items. Optional filters:
    - `status=watchlist|watched`
    - `tonight=true` (returns only eligible items for sessions).
- `PATCH /watchlist-items/{item_id}`
  - Update `status`, `snoozed_until`, or `remove` item.

Snoozed items are excluded from "tonight" pools until the snooze expires.

### Tonight sessions

Sessions are time-boxed pickers that freeze a candidate deck and collect votes.

1) Create a session: `POST /groups/{group_id}/sessions`
   - Validates membership.
   - Canonicalizes constraints with `TonightConstraints`.
   - Optionally calls OpenAI to parse free-text constraints.
   - Builds an eligible pool from watchlist (`watchlist` + not snoozed).
   - Applies hard filters (format, max_runtime).
   - Deterministically shuffles and slices a preliminary set.
   - Optionally calls OpenAI to rerank candidates.
   - Freezes final deck into `tonight_session_candidates`.

2) Vote: `POST /sessions/{session_id}/vote`
   - One vote per user; votes can be updated.
   - Votes are blind (no tallies exposed in session state).

3) Get state: `GET /sessions/{session_id}`
   - Returns status, deck, and result if completed.
   - Auto-resolves if `ends_at` passed.

4) Shuffle shortcut: `POST /sessions/{session_id}/shuffle`
   - Immediately completes session with deterministic winner.

#### Resolution rules

When a session expires:
- Winner is the item with max YES.
- Ties break by min NO.
- If still tied, deterministic random (seeded by session ID).
- If nobody voted, deterministic random from the deck.

### TMDB search

`GET /tmdb/search?q=<query>&type=multi`

Uses TMDB API with a short in-memory cache (10 minutes) and returns a compact list used by the UI.

### MCP (Model Context Protocol)

The app mounts MCP at `/mcp` using streamable HTTP. It exposes all OpenAPI operations as MCP tools. Typical flow:

1) Initialize (creates a session and returns `mcp-session-id` header).
2) Use that session id for `tools/list` and `tools/call` requests.

This allows MCP-capable clients (agents, CLIs, etc.) to discover and call your API.

## Configuration

Required env vars (see `app/core/config.py`):

- `DATABASE_URL` (async SQLAlchemy URL)
- `JWT_SECRET`
- `TMDB_TOKEN`

Common optional vars:

- `ENV` (default `local`)
- `JWT_ALGORITHM` (default `HS256`)
- `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30 days)
- `CORS_ORIGINS` (comma-separated)
- `OPENAI_API_KEY` (optional)
- `OPENAI_MODEL` (default `gpt-5-mini`)
- `OAUTH_GOOGLE_CLIENT_ID`
- `OAUTH_GOOGLE_CLIENT_SECRET`
- `OAUTH_FACEBOOK_CLIENT_ID`
- `OAUTH_FACEBOOK_CLIENT_SECRET`
- `OAUTH_GOOGLE_CALLBACK_URL` (default `http://localhost:8000/auth/google/callback`)
- `OAUTH_FACEBOOK_CALLBACK_URL` (default `http://localhost:8000/auth/facebook/callback`)
- `OAUTH_FRONTEND_SUCCESS_URL` (default `http://localhost:5173/app`)
- `OAUTH_FRONTEND_FAILURE_URL` (default `http://localhost:5173/login`)
- `OAUTH_SESSION_SECRET` (falls back to `JWT_SECRET` if unset)

## Running tests

```bash
pytest
```

Tests use a dedicated test database defined by `DATABASE_URL` in the environment. In tests, the schema is created and dropped automatically.

## Operational notes

- Auth is cookie-based (`access_token`).
- Most routes require group membership checks in the service layer.
- Watchlist items are unique per `(group_id, title_id)`.
- AI behavior is optional; sessions still work without OpenAI.
- Deterministic shuffling helps reproducibility and test stability.

## Troubleshooting

- Migration errors: verify `DATABASE_URL` and run `alembic upgrade head`.
- Auth failures: ensure cookies are preserved by your client.
- TMDB search empty: check `TMDB_TOKEN`.
- AI parsing/rerank failures: check `OPENAI_API_KEY` and logs; failures fall back gracefully.

## Sequence diagrams

### Signup, login, and auth cookie

```mermaid
sequenceDiagram
  participant Client
  participant API
  participant DB

  Client->>API: POST /auth/register
  API->>DB: INSERT user
  DB-->>API: user id
  API-->>Client: 201 {id}

  Client->>API: POST /auth/login
  API->>DB: SELECT user by email
  DB-->>API: user row
  API-->>Client: 200 + Set-Cookie access_token

  Client->>API: GET /me (cookie)
  API->>DB: SELECT user by id
  DB-->>API: user row
  API-->>Client: 200 user profile
```

### Friend invite and accept

```mermaid
sequenceDiagram
  participant A as User A
  participant B as User B
  participant API
  participant DB

  A->>API: POST /friends/invite
  API->>DB: INSERT friend_invite
  DB-->>API: code
  API-->>A: 201 {code}

  B->>API: POST /friends/accept {code}
  API->>DB: SELECT invite (lock)
  API->>DB: INSERT friendship (if new)
  API->>DB: UPDATE invite uses_count
  API-->>B: 200 {ok:true}
```

### Group creation and invite

```mermaid
sequenceDiagram
  participant Owner
  participant Member
  participant API
  participant DB

  Owner->>API: POST /groups {member_user_ids}
  API->>DB: validate friendships
  API->>DB: INSERT group + memberships
  API-->>Owner: 201 group

  Owner->>API: POST /groups/{id}/invite
  API->>DB: INSERT group_invite
  API-->>Owner: 201 {code}

  Member->>API: POST /groups/accept-invite {code}
  API->>DB: INSERT membership
  API-->>Member: 200 {ok:true}
```

### Tonight session lifecycle

```mermaid
sequenceDiagram
  participant Client
  participant API
  participant DB
  participant OpenAI

  Client->>API: POST /groups/{id}/sessions {constraints, text?}
  API->>DB: load watchlist + apply filters
  API->>OpenAI: parse constraints (optional)
  API->>OpenAI: rerank candidates (optional)
  API->>DB: INSERT session + candidates
  API-->>Client: 201 session + deck

  Client->>API: POST /sessions/{id}/vote
  API->>DB: UPSERT vote
  API-->>Client: 200 {ok:true}

  Client->>API: GET /sessions/{id}
  API->>DB: auto-resolve if expired
  API-->>Client: 200 session state
```

## Example curl flows

### Auth + /me

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"a@example.com","username":"auser","display_name":"A","password":"SuperSecret123"}'

curl -i -c cookies.txt -b cookies.txt -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"a@example.com","password":"SuperSecret123"}'

curl -i -b cookies.txt http://127.0.0.1:8000/me
```

### Friends + group

```bash
# A creates invite
curl -i -b cookies.txt -X POST http://127.0.0.1:8000/friends/invite

# B accepts invite (use B's cookie jar)
curl -i -b cookies_b.txt -X POST http://127.0.0.1:8000/friends/accept \
  -H 'Content-Type: application/json' \
  -d '{"code":"<INVITE_CODE>"}'

# A creates group with B
curl -i -b cookies.txt -X POST http://127.0.0.1:8000/groups \
  -H 'Content-Type: application/json' \
  -d '{"name":"Movie Night","member_user_ids":["<B_USER_ID>"]}'
```

### Watchlist + tonight session

```bash
# Add TMDB item to group
curl -i -b cookies.txt -X POST http://127.0.0.1:8000/groups/<GROUP_ID>/watchlist \
  -H 'Content-Type: application/json' \
  -d '{"type":"tmdb","tmdb_id":603,"media_type":"movie","title":"The Matrix","year":1999,"poster_path":"/m.jpg"}'

# Start a session
curl -i -b cookies.txt -X POST http://127.0.0.1:8000/groups/<GROUP_ID>/sessions \
  -H 'Content-Type: application/json' \
  -d '{"constraints":{"format":"any"},"text":"something fun","duration_seconds":90,"candidate_count":5}'

# Vote
curl -i -b cookies.txt -X POST http://127.0.0.1:8000/sessions/<SESSION_ID>/vote \
  -H 'Content-Type: application/json' \
  -d '{"watchlist_item_id":"<WATCHLIST_ITEM_ID>","vote":"yes"}'
```

### MCP discovery

```bash
curl -i -H 'Accept: application/json' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"curl","version":"0.0.0"},"protocolVersion":"2024-11-05","capabilities":{}}}' \
  http://127.0.0.1:8000/mcp

curl -i -H 'Accept: application/json' -H 'Content-Type: application/json' \
  -H 'mcp-session-id: <SESSION_ID_FROM_RESPONSE>' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  http://127.0.0.1:8000/mcp
```

## API reference

### Auth

- `POST /auth/register` -> `{id}`
- `POST /auth/login` -> `{ok:true}` (sets `access_token` cookie)
- `POST /auth/logout` -> `{ok:true}`
- `GET /me` -> current user

### Friends

- `POST /friends/invite` -> `{code, expires_at}`
- `POST /friends/accept` -> `{ok:true}`
- `GET /friends` -> list friends

### Groups

- `POST /groups` -> group list item
- `GET /groups` -> list groups
- `GET /groups/{group_id}` -> group detail + members
- `POST /groups/{group_id}/invite` -> `{code, expires_at, max_uses, uses_count}`
- `POST /groups/accept-invite` -> `{ok:true}`

### Watchlist

- `POST /groups/{group_id}/watchlist` -> watchlist item
- `GET /groups/{group_id}/watchlist` -> list items
  - Query: `status=watchlist|watched`
  - Query: `tonight=true|false`
- `PATCH /watchlist-items/{item_id}` -> `{ok:true, removed:boolean}`
  - Body: `status`, `snoozed_until`, `remove`

### Sessions

- `POST /groups/{group_id}/sessions` -> session + deck
- `GET /sessions/{session_id}` -> session state
- `POST /sessions/{session_id}/vote` -> `{ok:true}`
- `POST /sessions/{session_id}/shuffle` -> completed session state

### TMDB

- `GET /tmdb/search?q=<query>&type=multi` -> compact TMDB results

### Health

- `GET /health` -> service status

### MCP

- `POST /mcp` -> JSON-RPC (initialize, tools/list, tools/call)
- `GET /mcp` -> SSE stream (requires `Accept: text/event-stream` and `mcp-session-id`)
