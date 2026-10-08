# Public Player Photo Image Endpoint Specification

## 1. Purpose

This specification changes the authorization behavior of one existing Player-photo
endpoint so a browser can render an image with a plain `<img src>` request.

The implementation must be made on the current `main` branch. Keep the change small,
reviewable, and aligned with the current `AGENTS.md`.

## 2. Approved authorization change

Make this endpoint public in every environment:

```text
GET /players/{player_id}/photos/{photo_id}
```

It must not require an access token, an administrator role, a cookie, a signed URL, or
any other client credential.

The following endpoints remain administrator-only and must retain their existing
authentication and authorization behavior:

```text
POST   /players/{player_id}/photos
GET    /players/{player_id}/photos
DELETE /players/{player_id}/photos/{photo_id}
```

## 3. Explicitly accepted exposure model

The public image URL continues to contain the existing numeric `player_id` and
`photo_id`. URLs are intentionally enumerable. Do not introduce public UUIDs, signed
URLs, opaque tokens, temporary links, cookie authentication, or a new access-control
mechanism for this task.

This behavior is approved for all deployment environments for the current project
scope. It must be documented as a deliberate trade-off, not presented as secure image
access.

## 4. Endpoint behavior

On a successful request, return the complete stored image with status `200` and its
stored media type (`image/png` or `image/jpeg`).

Continue to set:

```text
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

Continue to avoid exposing storage internals:

- no internal UUID filename in response headers;
- no `ETag` derived from the file;
- no `Last-Modified` value derived from the filesystem;
- no range or conditional response behavior outside the documented `200` response.

If the Player does not exist, the photo does not belong to that Player, the photo row
does not exist, or its stored file cannot be found, preserve the safe existing `404`
behavior. Do not disclose whether a numeric identifier exists for another Player.

When a Player is disabled, the existing business rule deletes current photo rows and
files. A previous public image URL must then return `404`.

## 5. Implementation constraints

- Remove authorization only from the binary image `GET` route.
- Do not alter upload, gallery listing, deletion, Player-disable cleanup, file storage,
  image validation, database schema, migrations, or the maximum of three photos.
- Do not add a dependency, framework, database object, token format, cache, queue, or
  infrastructure.
- Do not weaken the existing safe error envelope or expose filesystem paths.
- Preserve the route/service/model layering and current error mapping patterns.

## 6. OpenAPI and documentation

Update the public API contract so the image-download operation has no security
requirement. The remaining Player-photo operations must continue to show administrator
access security.

Regenerate and commit `docs/openapi.json`; never hand-edit it.

Update `documentation.md` to state clearly that the individual image endpoint is public
and intended for direct browser image rendering, while gallery management remains
administrator-only. State the accepted enumerability trade-off accurately.

Update `AGENTS.md` only if its Player-photo authorization rules would otherwise become
inaccurate.

## 7. Required tests

Add or update tests covering at least:

1. An unauthenticated request to `GET /players/{player_id}/photos/{photo_id}` returns
   the expected image with `200`.
2. The same endpoint remains accessible when an invalid or absent bearer token would
   otherwise fail authentication.
3. Upload, list, and delete remain rejected without administrator authorization.
4. A photo belonging to another Player, a nonexistent photo, and a deleted/disabled
   Player photo return the existing safe `404` behavior.
5. The public image response retains `Cache-Control: no-store` and
   `X-Content-Type-Options: nosniff`, and does not expose the UUID filename, `ETag`, or
   `Last-Modified`.
6. The generated OpenAPI contract documents no security requirement for the image GET
   and preserves security for the other three operations.

Run the focused Player-photo and API-contract tests, then the complete backend test
suite. Run `git diff --check` before finishing.

## 8. Out of scope

- Player identity recognition or embeddings.
- Signed, expiring, opaque, or UUID-based image URLs.
- Environment-specific authorization behavior.
- Frontend implementation.
- Changes to storage retention, image limits, disable behavior, or database design.
