# Arbiter Route Security Matrix

Audit date: 2026-07-21. `cookie` means the API derives identity from the
validated `HttpOnly` access cookie and its active database session. Unsafe HTTP
methods require an exact configured `Origin`; all request bodies are capped at
64 KiB before parsing (feedback remains capped at 16 KiB). Production Redis
limits fail closed.

## Public HTTP

| Method | Path | Authentication / authorization | Abuse and data controls | Returned data |
| --- | --- | --- | --- | --- |
| GET | `/health` | Public | No state; minimal cacheable response | `{status}` only |
| POST | `/auth/register` | Public | IP limit; strict bounded schema | New public account identity |
| POST | `/auth/login` | Public | IP + normalized-subject limit; constant-work failure | Generic success + cookie |
| POST | `/auth/magic-link/request` | Public | IP + subject limit; generic response; browser-intent cookie | Generic success |
| POST | `/auth/magic-link/verify` | One-time hashed grant + matching intent | Atomic consume, expiry, JSON only | Generic success + cookie |
| GET | `/auth/google/login` | Public | IP limit; Authlib state session | Google redirect |
| GET | `/auth/google/callback` | OAuth state; verified email + immutable Google subject | One-time existing-account link only for authoritative Gmail/Workspace claims; exact redirects | Frontend redirect + cookie |
| POST | `/auth/local-bypass` | Local/test only | Constant-time configured token | Generic success + cookie |
| POST | `/auth/logout` | Optional active cookie | Revokes JTI and closes user sockets | Generic success |
| POST | `/feedback` | Optional cookie | Feature gate; 16 KiB; honeypot; Redis; idempotency | Generic success |
| GET | `/mood-cues` | Public | Static allowlisted catalogue | Cue labels and IDs |

Production does not expose `/docs`, `/redoc`, `/openapi.json`, local bypass, or
the development-only MCP transport.

## Account and Social

| Method/path family | Authorization | Rate limit | Response/data rule |
| --- | --- | --- | --- |
| `GET/PATCH /me`, `PATCH /me/avatar` | Current cookie user | Global body/field limits | Own account only |
| `DELETE /me` | Current user; exact confirmation; no owned groups | Global limits | No body; revokes sockets/session |
| `POST /friends/requests` | Current user; target resolved server-side | Account + IP | Email lookup is enumeration-resistant; public username may return not found |
| `GET /friends/requests`, `GET /friends` | Current involved user | Global limits | Public identity only; no email |
| `POST/DELETE /friends/requests/{id}...` | Recipient decides; creator revokes | Global limits | Generic state; idempotent where supported |
| `POST /friends/unfriend` | Current user must be one party | Global limits | Generic result |
| `GET/POST/DELETE /friends/.../block` | Current blocker; target exists | Global limits | Public identity / generic result |
| `GET/POST/DELETE /group-invites...` | Inviter, target, or group owner as applicable | Creation: account + IP | No token/link; public identities only |

Legacy bearer friend/group invite-link and direct-membership endpoints are
removed. Current invitations are authenticated in-app records.

## Groups, Watchlists, Sessions, History, and Insights

| Method/path family | Server authorization | Resource/data controls |
| --- | --- | --- |
| `POST/GET /groups` | Current user; lists memberships only | Creation: account 10/hour, IP 30/hour; strict names; bounded by memberships |
| `GET/PATCH/DELETE /groups/{id}` | Member read; owner update/delete | Public member identity only; socket revocation on loss |
| `POST /groups/{id}/leave` | Member; owner must transfer/delete | Transactional |
| `POST /groups/{id}/transfer-ownership` | Owner; target is member | Transactional |
| `POST /groups/{id}/invites` | Owner; target is friend | Account + IP; pending uniqueness |
| `GET/POST /groups/{id}/watchlist` | Group member | Page max 100; strict title schema |
| `PATCH /watchlist-items/{id}` | Item's group member | Explicit patch fields only |
| `GET /tmdb/search` | Current user | Account 60/min, IP 180/min; query 100 chars; cache 512 |
| `POST /groups/{id}/sessions` | Group member | Account 30/hour, IP 90/hour; candidates max 30; duration max 600s; strict criteria |
| `/sessions/{id}` read/vote/shuffle | Group member/participant | Vote: account 180/min, IP 540/min; UUIDs, vote uniqueness, state transitions |
| `POST /sessions/{id}/end` | Group owner | State transition checks |
| `PATCH /sessions/{id}/watch-party` | Group owner | HTTPS exact Teleparty hosts; 2 KiB URL |
| `POST /sessions/{id}/watch-party/handoff` | Group member | Idempotent; no historical URL exposure |
| `/sessions/{id}/completion...` | Group member | Transactional, idempotent; explicit schema |
| `GET /groups/{id}/movie-nights` | Group member | Cursor, limit max 50; no raw votes/source IDs |
| `GET /groups/{id}/insights` | Group member | Fixed range; aggregate data only |
| `GET /groups/{id}/movie-details/{reference}` | Group member | Authorized context; upstream timeout/cache |
| `GET /groups/{id}/movie-night-artwork/{candidate}` | Member; candidate is completed winner in group | Fixed TMDB path/extension; raster only; 10 MiB streamed cap |

## WebSockets

| Path | Pre-accept controls | Room derivation | Client messages and loss of access |
| --- | --- | --- | --- |
| `/me/ws` | Exact Origin, active cookie/JTI/user | Authenticated user ID only | Exact JSON ping only; eight connections/user; logout closes |
| `/groups/{id}/watchlist/ws` | Exact Origin, active cookie, membership | Authorized path group | Exact ping; 4 KiB server frame limit; membership loss closes 1008 |
| `/sessions/{id}/ws` | Exact Origin, active cookie, session authorization | Authorized session/group | Exact ping; 4 KiB server frame limit; affected access only closes |

All hubs are process-local. The Render command intentionally uses one worker.
Multiple workers or service instances require shared broker fan-out before
scaling.
