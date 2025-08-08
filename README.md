# Media Requests (Overseerr / Sonarr+Radarr)

A Home Assistant custom integration to request **movies** and **TV shows** via:
- **Overseerr** (search → request), with **serverId** and **profileId** defaults & per-call overrides
- **Radarr/Sonarr** directly (ARR backend: movies → Radarr, TV → Sonarr)

## Highlights
- **One service**: `overseerr.request_media`
- **Config Flow (UI)** with backend selection
  - Overseerr path now has a **two-step** flow:
    1) Enter Base URL + API Key (+ default TV seasons)
    2) Pick optional defaults from **live dropdowns**: Overseerr **server** and **movie/TV profile IDs**
- **Options Flow**
  - Change **Default TV seasons** (Season 1 or All)
  - Manage **ARR presets** (UHD, Kids, etc.)
  - **Overseerr defaults** (server + movie/TV profiles) from live dropdowns
- **ARR Presets** per call + overrides (quality/lang/root)
- **TV seasons**: per-call `"all"` or `[1,2,…]`, or default (Season 1/All)

See `services.yaml` for parameters and examples.
