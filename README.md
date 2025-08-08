# Hassarr Media Requests (Overseerr / Sonarr+Radarr)

Unified Home Assistant custom integration to request **movies** and **TV shows** via either:
* **Overseerr** (search → request) with optional default `serverId` / `profileId` and per-call overrides.
* **Direct ARR** (Radarr for movies, Sonarr for TV) with presets + overrides.

> Migration: The integration folder and domain have been renamed from `overseerr` to `hassarr`. Update automations: service call becomes `hassarr.request_media` and events become `hassarr_request_complete` / `hassarr_request_failed`.

## Highlights
* **One service**: `hassarr.request_media`
* **Search + request** from a single call; supports `query` as free text or `tmdb:<id>`.
* **Two backends** selectable at config time.
* **Config Flow (UI)**:
  * Overseerr path: two-step (credentials → optional defaults fetched live).
  * ARR path: single step for Radarr + Sonarr.
* **Options Flow**:
  * Change Default TV Seasons (Season 1 or All)
  * Manage JSON **ARR presets** (UHD, Kids, etc.)
  * Update Overseerr default server + movie/TV profiles (live dropdowns)
* **ARR Presets + Overrides**: quality profile, language profile (TV), root folder.
* **TV seasons**: per-call `'all'`, list like `[1,2,5]`, or default (Season 1 / All).
* **Events emitted**:
  * `hassarr_request_complete` on success
  * `hassarr_request_failed` on failure

## Service: `hassarr.request_media`

Fields (see `services.yaml`):

| Field | Required | Notes |
|-------|----------|-------|
| query | yes | Title or `tmdb:<id>` |
| media_type | yes | `movie`, `tv`, or `show` (alias to `tv`) |
| seasons | no | TV only: `all` or list `[1,2]`; omitted = default |
| is_4k | no | Overseerr backend only |
| overseerr_server_id / overseerr_profile_id | no | Override defaults (Overseerr backend) |
| profile_preset | no | Name of ARR preset (ARR backend) |
| quality_profile_id / language_profile_id / root_folder_path | no | Per-call overrides (ARR backend) |

### Example Automations

Movie via Overseerr (explicit profile override):
```yaml
service: hassarr.request_media
data:
  query: "Inception"
  media_type: movie
  overseerr_profile_id: 6
```

TV show first season via ARR preset:
```yaml
service: hassarr.request_media
data:
  query: "Severance"
  media_type: tv
  profile_preset: "UHD"
```

Request all seasons via tmdb id:
```yaml
service: hassarr.request_media
data:
  query: "tmdb:1402"  # The Walking Dead
  media_type: tv
  seasons: all
```

React to success event:
```yaml
trigger:
  - platform: event
    event_type: hassarr_request_complete
action:
  - service: persistent_notification.create
    data:
      title: "Media Requested"
      message: >-
        {{ trigger.event.data.media_type }}: {{ trigger.event.data.query }} (TMDB {{ trigger.event.data.tmdb_id }})
```

## ARR Preset Schema
`Options` → `Presets JSON` expects an array like:
```json
[
  {
    "name": "UHD",
    "radarr": {"root": "/movies/UHD", "quality_profile_id": 9},
    "sonarr": {"root": "/tv/UHD", "quality_profile_id": 10, "language_profile_id": 2}
  },
  {
    "name": "Kids",
    "radarr": {"root": "/media/Kids/Movies", "quality_profile_id": 4},
    "sonarr": {"root": "/media/Kids/TV", "quality_profile_id": 5}
  }
]
```

## Events Payload
### Success: `hassarr_request_complete`
```json
{
  "backend": "overseerr" | "arr",
  "media_type": "movie" | "tv",
  "query": "Original query",
  "tmdb_id": 12345,
  "response": {"...raw response..."}
}
```
### Failure: `hassarr_request_failed`
```json
{
  "backend": "overseerr" | "arr",
  "media_type": "movie" | "tv",
  "query": "Original query",
  "error": "Error message"
}
```

## Notes
* Prefer `tmdb:<id>` for ambiguous titles.
* For exact Overseerr match rules or ARR quality profile creation, configure directly in those apps.
* Timeout defaults to 15s with light retry on 502/503/504.

## Roadmap / Ideas
* Sensor with pending request counts
* Optional unmonitored add mode
* User-selectable matching strategy (exact title vs popularity)

---
MIT License. See `LICENSE`.
