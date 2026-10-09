# Phone bridge API (`/api/v1`)

The desktop UI is bound to `127.0.0.1` only. When the user turns on **«На телефон» → «Доступ с телефона»**,
PyLoader starts a second, read-mostly server on the local network (`0.0.0.0:5001`, override with
`PYLOADER_BRIDGE_PORT`). Today a phone browser uses it through a minimal page; the future PyLoader mobile
app should use the same endpoints, so this document is the contract.

## Pairing

The QR code on the computer encodes:

```
http://<lan-ip>:<port>/connect?token=<token>
```

* A browser opening it gets an `HttpOnly` cookie `pyloader_token` (1 year) and is redirected to `/`.
* An app should parse `host`, `port` and `token` from the URL, store them, and send
  `Authorization: Bearer <token>` with every request (`?token=` also works, e.g. for media players
  that cannot set headers).
* «Сменить ключ доступа» on the computer rotates the token; every paired device then gets `401`
  and must scan again. Treat `401` as "re-pair required".
* The bridge only exists while the user keeps it enabled (it is re-enabled on the next launch if it
  was on). Use `GET /api/v1/ping` to check reachability.

All JSON responses have `ok: true|false`; errors carry a human-readable Russian `error`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/ping` | `{name, version, api: 1, device}` — server identity and API level |
| GET | `/api/v1/outbox` | Items the user explicitly sent to the phone, newest first, with `sent_at` / `delivered_at` |
| POST | `/api/v1/outbox/<id>/received` | Mark an outbox item as saved on the device (download also marks it) |
| GET | `/api/v1/library?type=all\|audio\|video\|favorites&search=&sort=date\|name\|artist\|duration\|size` | Whole library |
| GET | `/api/v1/playlists` | User playlists (`id, name, items[], count, duration, updated_at`) and YouTube collections |
| GET | `/api/v1/playlists/<id>` | One playlist with resolved items, in order |
| GET | `/api/v1/collections/items?name=<collection>` | Items of an auto-collection (a downloaded YouTube playlist), in playlist order |
| GET | `/api/v1/media/<id>` | One item |
| GET | `/api/v1/media/<id>/stream` | Inline file with HTTP `Range` support — use for playback |
| GET | `/api/v1/media/<id>/download` | Attachment download (resumable with `Range`); marks the outbox item delivered |
| GET | `/api/v1/media/<id>/cover` | Cover image (`404` when there is none) |

### Media item

```json
{
  "id": "3f9c2a1b7d4e",
  "title": "Midnight Drive",
  "artist": "Neon Coast",
  "media_type": "audio",
  "format": "mp3",
  "duration": 187.3,
  "size": 4521984,
  "modified": 1791405885.3,
  "video_id": "dQw4w9WgXcQ",
  "playlist_title": "Lo-fi Mix",
  "favorite": false,
  "stream_url": "/api/v1/media/3f9c2a1b7d4e/stream",
  "download_url": "/api/v1/media/3f9c2a1b7d4e/download",
  "cover_url": "/api/v1/media/3f9c2a1b7d4e/cover",
  "sent_at": "2026-10-07T12:00:00+00:00",
  "delivered_at": null
}
```

`sent_at` / `delivered_at` appear only in `/outbox`. URLs are relative to the bridge base URL.

`id` is stable across restarts and survives the file being renamed or moved outside PyLoader as long
as its YouTube ID and format stay the same, so an app can key its offline cache on it and use
`size` + `modified` to detect changes.

## Suggested app sync loop

1. On launch: `ping`; on `401` show the pairing (QR) screen.
2. Pull `outbox` → download every item without a local copy via `download_url` (resume with `Range`),
   then `POST /outbox/<id>/received`.
3. Pull `playlists` and `library?type=audio` for browsing; stream with `stream_url` or play the
   local copy when present.
4. Poll every 30–60 s while the app is in the foreground (no push channel yet).

## Security notes

* Read-only except for marking outbox items received; no deletion or downloads can be started from the phone.
* LAN only, plain HTTP: anyone on the same network who has the token can read the library. Rotate
  the token if a device is lost; turn the bridge off on untrusted networks.
* The desktop API rejects requests whose `Host` is not `127.0.0.1` / `localhost` (DNS-rebinding guard).
