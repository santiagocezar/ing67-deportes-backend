# API error contract

All API errors use the same JSON structure:

```json
{
  "error": {
    "code": "validation_error",
    "message": "birthdate must be a valid date in YYYY-MM-DD format."
  }
}
```

The status selection follows the
[MDN HTTP status reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status):

| Status | Use in this API |
| --- | --- |
| `400 Bad Request` | The request body is missing or malformed. |
| `401 Unauthorized` | Authentication is missing, invalid, expired, revoked, or a refresh token was reused. |
| `403 Forbidden` | The user is authenticated but lacks permission for an operation. Reserved for protected business endpoints. |
| `404 Not Found` | The requested resource does not exist. |
| `409 Conflict` | The request conflicts with current state, such as an email already registered. |
| `422 Unprocessable Content` | JSON is valid but a field fails semantic validation. |
| `503 Service Unavailable` | Authentication or the database is temporarily unavailable. |

Frontend code should use `error.code` for control flow and `error.message` for a
human-readable fallback. It should not branch on the English message text.
