DOMAIN = "hassarr"

# Service name
SERVICE_REQUEST_MEDIA = "request_media"

# Events
EVENT_REQUEST_COMPLETE = f"{DOMAIN}_request_complete"
EVENT_REQUEST_FAILED = f"{DOMAIN}_request_failed"

# Backend selection
CONF_BACKEND = "backend"  # "overseerr" | "arr"

# Overseerr config
CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"

# Overseerr extra defaults
CONF_OVERSEERR_SERVER_ID = "overseerr_server_id"              # int or None (legacy)
CONF_OVERSEERR_SERVER_ID_RADARR = "overseerr_radarr_server_id"  # int or None
CONF_OVERSEERR_SERVER_ID_SONARR = "overseerr_sonarr_server_id"  # int or None
CONF_OVERSEERR_PROFILE_ID_MOVIE = "overseerr_profile_id_movie"  # int or None
CONF_OVERSEERR_PROFILE_ID_TV = "overseerr_profile_id_tv"        # int or None

# Service-call overrides for Overseerr
CONF_OVERSEERR_SERVER_ID_OVERRIDE = "overseerr_server_id"
CONF_OVERSEERR_PROFILE_ID_OVERRIDE = "overseerr_profile_id"

# ARR defaults (used if backend == "arr")
CONF_RADARR_URL = "radarr_url"
CONF_RADARR_KEY = "radarr_api_key"
CONF_RADARR_ROOT = "radarr_root"
CONF_RADARR_PROFILE = "radarr_quality_profile_id"

CONF_SONARR_URL = "sonarr_url"
CONF_SONARR_KEY = "sonarr_api_key"
CONF_SONARR_ROOT = "sonarr_root"
CONF_SONARR_PROFILE = "sonarr_quality_profile_id"

# Options: multiple presets and default seasons
CONF_PRESETS = "presets"  # list[dict], see README for schema
CONF_DEFAULT_TV_SEASONS = "default_tv_seasons"  # "season1" | "all"
DEFAULT_TV_SEASONS_CHOICES = ["season1", "all"]

# Service-time overrides / selectors (ARR backend)
CONF_PROFILE_PRESET = "profile_preset"
CONF_QUALITY_PROFILE_ID = "quality_profile_id"      # movie or tv
CONF_ROOT_FOLDER_PATH = "root_folder_path"

# Runtime storage
STORAGE_CLIENT = "client"
STORAGE_BACKEND = "backend"
