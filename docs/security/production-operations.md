# Security Operations

## Required production controls

- Keep one Uvicorn worker and one Render service instance until realtime hubs use a
  shared broker. The checked-in start command enforces WebSocket and concurrency limits.
- Provision a same-region Render Key Value instance and set its internal URL as
  `RATE_LIMIT_REDIS_URL`. Production authentication, social, TMDB, and feedback
  protections fail closed when this service is unavailable.
- Keep `CORS_ORIGINS` to exact HTTPS frontend origins. Preview origins must be added
  explicitly; wildcard and regex production origins are rejected.
- Use a randomly generated `JWT_SECRET` of at least 32 bytes and a distinct
  `OAUTH_SESSION_SECRET`. Rotate both through a planned sign-out event.
- Keep FastAPI docs, OpenAPI, MCP, local auth bypass, SQL echo, and source maps disabled.

### Render Key Value setup

1. In the Render dashboard select **New > Key Value** in the same region as the API.
2. Keep public access disabled and copy the internal Redis URL.
3. Open `arbiter-api > Environment`, set `RATE_LIMIT_REDIS_URL` to that internal URL,
   and redeploy. Do not use a `VITE_` variable or expose this value to the frontend.
4. Confirm a normal auth request works, then exercise a test identifier until the API
   returns `429` with `Retry-After`. Clear only that test key after verification.
5. Keep the Render service at one instance/worker; Key Value shares limiter state but
   does not make the process-local WebSocket hubs horizontally safe.

Current launch limits:

| Operation | Window | Limits |
| --- | --- | --- |
| Login | 15 minutes | 30/IP and 10/normalized account identifier |
| Registration | 1 hour | 5/IP |
| Magic-link email | 15 minutes | 10/IP and 3/normalized email |
| OAuth start | 15 minutes | 20/IP |
| Friend request | 1 hour | 10/account and 30/IP |
| Group invite | 1 hour | 30/account and 60/IP |
| Group creation | 1 hour | 10/account and 30/IP |
| Session setup | 1 hour | 30/account and 90/IP |
| Vote submission | 1 minute | 180/account and 540/IP |
| TMDB search | 1 minute | 60/account and 180/IP |
| Feedback | 15 minutes | signed out 3/IP; signed in 5/account and 10/IP |

Identifiers stored in Key Value are HMAC digests, not raw email, user ID, or IP.

## Database and backups

- The application requires TLS for production Postgres and bounds connection/query time.
- Render paid Postgres provides managed backups and point-in-time recovery according to
  the selected database plan. Confirm recovery retention in the dashboard before launch.
- Run a restore drill into an isolated database before public launch and quarterly after.
- Run `alembic upgrade head` only through the pre-deploy command; inspect downgrade and
  lock behavior for every migration.

## Security response

- Rotate a credential immediately if it appears in source, CI output, build artifacts,
  browser bundles, or public logs. Removing it from Git does not revoke it.
- Preserve minimal event metadata for authorization denials, rate-limit triggers, origin
  failures, and server errors. Never log cookies, tokens, feedback text, or private URLs.
- Review dependency and secret-scan CI failures before merging; do not blanket-ignore them.
- If scaling past one instance, move realtime fan-out and all limiter state to shared
  infrastructure before enabling the additional instance.
