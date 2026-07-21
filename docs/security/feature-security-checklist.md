# Arbiter Feature Security Checklist

Use this checklist for every feature, including Annual Recap. A checked item means
there is code or an automated test supporting it, not merely an assumption.

## Data and authorization

- Classify each field as public, group-private, user-private, credential, or secret.
- Derive user identity from the validated server session, never request data.
- Check object membership/ownership for every read and mutation.
- Add a cross-user identifier-substitution test.
- Return an explicit response schema containing only fields the client needs.
- Define deletion and historical-retention behavior before persisting new data.

## Inputs and outputs

- Use a strict request schema with bounded strings, numbers, collections, and pages.
- Treat third-party responses as untrusted and validate their shape.
- Keep user text as text; do not introduce HTML rendering for convenience.
- Allowlist redirect targets, external hosts, sort fields, and enum-like values.
- Never put credentials, invite tokens, OAuth codes, or private URLs in logs or URLs.

## Browser and realtime boundaries

- Require an approved Origin for cookie-authenticated browser mutations.
- Add the minimum CSP sources required by new frontend resources.
- Preserve `HttpOnly`, `Secure`, and the configured `SameSite` cookie policy.
- Authenticate and authorize WebSockets before acceptance; minimize event payloads.
- Recheck or terminate realtime access when authorization changes.
- Verify logout and Back navigation cannot reuse protected state.

## Abuse and resources

- Identify the actor and cost of the operation.
- Add shared rate limits for public, email-producing, lookup, and expensive operations.
- Bound request bodies, arrays, pagination, remote responses, and generated images.
- Set connect/read/total timeouts for external calls.
- Make retries idempotent when duplicate work has a security or financial cost.

## Privacy and release evidence

- Check browser storage, query caches, WebSocket frames, exports, and analytics payloads.
- Test that generated/downloaded artifacts exclude private fields by construction.
- Add success, denial, malformed-input, and provider-failure tests.
- Run dependency, secret, static-analysis, lint, type-check, test, and build gates.
- Record any residual risk and operational configuration required for production.

## Annual Recap additions

- Build recap data only from group-authorized completed-session snapshots.
- Do not expose per-user votes, private handles, participant names, or avatars by default.
- Apply the Movie Night Card export sanitizer to every recap image payload.
- Keep any public sharing capability separate, revocable, unguessable, and opt-in.
- Version deterministic calculations and test missing/legacy records.
