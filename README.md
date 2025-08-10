# Hassarr Media Requests

** **Full dislosure: Vibe-Coded with GPT5. 
Originally forked from: TegridyTate/Hassarr, but completely wiped and built from scratch.** **


Home Assistant custom integration to request Movies and TV shows via:
- Overseerr backend (requests go to Overseerr which talks to your Arr apps)
- Direct Arr backend (requests go straight to Radarr/Sonarr)

It fetches servers/roots/profiles from your backend and exposes them as Select entities. Service calls use those entities as defaults, so you can change behavior on the fly without editing YAML.

## Features
- Two* backends: Overseerr or Direct Arr (Sonarr+Radarr) (*Jellyfin to come)
- Auto-fetch and expose choices as Select entities:
  - Overseerr: default Radarr/Sonarr server, default Movie/TV profiles
  - Arr: default Radarr/Sonarr root folders, default quality profiles
- Default TV Seasons as a Select entity (Season 1 or All)
- “Backend in use” sensor (informational)
- Clean device grouping per entry (entities appear under one device)
- Single service: hassarr.request_media (uses entity selections as defaults)
- Accepts “tmdb:<id>” in query for precision
- Clear error messages and HA events on success/failure

## Installation
- HACS (recommended): Add this repository as a Custom Repository, then install “Hassarr Media Requests”.
- Manual: Copy custom_components/hassarr into your Home Assistant config/custom_components directory and restart Home Assistant.

## Configuration (Add Integration)
Pick one backend per entry. Each backend uses a 4-step guided setup.

Overseerr backend:
1) Base URL & API Key
2) Default Server
   - Overseerr Radarr Default Server
   - Overseerr Sonarr Default Server
3) Default Profile
   - Overseerr Default Movie Profile
   - Overseerr Default TV Profile
4) Default TV Season
   - Season 1 or All Seasons

Arr backend (direct Sonarr/Radarr):
1) URLs & API Keys (Radarr, Sonarr)
2) Default Root Folders (Radarr root, Sonarr root; dropdowns)
3) Default Profiles (Radarr quality, Sonarr quality)
4) Default TV Season (Season 1 or All)

After setup, entities are created under a device named:
- “Hassarr (Overseerr)” or
- “Hassarr (Sonarr/Radarr)”

## Entities
Select entities (used as service defaults):
- Overseerr:
  - Hassarr Radarr Server (serverId)
  - Hassarr Sonarr Server (serverId)
  - Hassarr Movie Profile (profileId)
  - Hassarr TV Profile (profileId)
- Arr:
  - Hassarr Radarr Root (path)
  - Hassarr Radarr Quality Profile (id)
  - Hassarr Sonarr Root (path)
  - Hassarr Sonarr Quality Profile (id)
- Common:
  - Hassarr Default TV Seasons (season1 | all)

Sensor:
- Hassarr Backend (overseerr | arr), informational only


## Service: `hassarr.request_media`

Fields (see `services.yaml`):

| Field | Required | Notes |
|-------|----------|-------|
| query | yes | Title or `tmdb:<id>` |
| media_type | yes | `movie`, or `tv` |
| seasons | no | TV only: `all` or list `[1,2]`; omitted = Default TV Seasons Entity |
| is_4k | no | Overseerr backend only, untested |
| overseerr_server_id / overseerr_profile_id | no | Override defaults (Overseerr backend), default =  Entity |
| quality_profile_id / root_folder_path | no | Override Defaults (ARR backend), default = Entity



Examples:
````yaml
# Minimal Overseerr movie
service: hassarr.request_media
data:
  query: "Dune (2021)"
  media_type: movie

# Minimal Arr TV (uses select entities and default seasons)
service: hassarr.request_media
data:
  query: "The Bear"
  media_type: tv

# Precise by TMDB id
service: hassarr.request_media
data:
  query: "tmdb:1399"
  media_type: tv

# One-off overrides (Arr)
service: hassarr.request_media
data:
  query: "Alien"
  media_type: movie
  quality_profile_id: 7
  root_folder_path: "/data/movies"
````

## Voice Automation: `YAML`

````yaml
alias: Media Request
mode: single
triggers:
  - trigger: conversation
    command:
      - Download show {title} season {seasons}
      - Download show {title} seasons {seasons}
      - Download show {title}
    id: show
  - trigger: conversation
    command:
      - Download movie {title}
    id: movie
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: movie
        sequence:
          - variables:
              title_clean: >-
                {{ (trigger.slots.title | default(trigger.sentence, true))
                   | regex_replace('(?i)^\\s*download\\s+movie\\s+', '')
                   | regex_replace('(?i)\\s+seasons?\\s+.*$', '')
                   | trim }}
          - action: hassarr.request_media
            data:
              media_type: movie
              query: "{{ title_clean }}"
      - conditions:
          - condition: trigger
            id: show
        sequence:
          - variables:
              # Start from the slot title, fall back to the sentence
              title_raw: "{{ trigger.slots.title | default(trigger.sentence, true) }}"
              # Strip leading keyword and any trailing "season(s) …"
              title_clean: >-
                {{ title_raw
                   | regex_replace('(?i)^\\s*download\\s+show\\s+', '')
                   | regex_replace('(?i)\\s+seasons?\\s+(all|\\d+(?:\\s*,\\s*\\d+)*)\\s*$', '')
                   | trim }}
              # Prefer captured slot; else extract from the sentence
              seasons_raw: >-
                {% if trigger.slots.seasons is defined %}
                  {{ trigger.slots.seasons }}
                {% else %}
                  {{ (trigger.sentence | regex_findall_index('(?i)\\bseasons?\\s+((?:all|\\d+(?:\\s*,\\s*\\d+)*))', 0))
                     | default('', true) }}
                {% endif %}
              seasons_parsed: >-
                {% set s = (seasons_raw | string | trim) %}
                {% if not s %}
                  {{ omit }}
                {% elif s | lower == 'all' %}
                  all
                {% else %}
                  [{{ (s | regex_findall('\\d+') | map('int') | list) | join(', ') }}]
                {% endif %}
          - action: hassarr.request_media
            data:
              media_type: tv
              query: "{{ title_clean }}"
              seasons: "{{ seasons_parsed }}"
````