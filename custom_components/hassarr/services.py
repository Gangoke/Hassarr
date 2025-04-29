import logging
import requests
import time
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, urlunparse
from .const import DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall

####
# Common
####

_LOGGER = logging.getLogger(__name__)

_RECENT_REQUESTS = {}

def fetch_data(url: str, headers: dict) -> dict | None:
    """Fetch data from the given URL with headers.

    Args:
        url (str): The URL to fetch data from.
        headers (dict): The headers to include in the request.

    Returns:
        dict | None: The JSON response if successful, None otherwise.
    """
    response = requests.get(url, headers=headers)
    if response.status_code == requests.codes.ok:
        return response.json()
    else:
        _LOGGER.error(f"Failed to fetch data from {url}: {response.text}")
        return None

def get_root_folder_path(url: str, headers: dict) -> str | None:
    """Get root folder path from the given URL.

    Args:
        url (str): The URL to fetch the root folder path from.
        headers (dict): The headers to include in the request.

    Returns:
        str | None: The root folder path if successful, None otherwise.
    """
    data = fetch_data(url, headers)
    if data:
        return data[0].get("path")
    return None

# Add this helper function to parse season input
def parse_seasons_input(seasons_input):
    """Parse seasons input into a list of integers or 'all'.
    
    Args:
        seasons_input: The input to parse
        
    Returns:
        "all" or a list of integers representing season numbers
    """
    # If already a list of integers, return as is
    if isinstance(seasons_input, list) and all(isinstance(item, int) for item in seasons_input):
        return seasons_input
        
    # If it's the string "all" (case insensitive)
    if isinstance(seasons_input, str) and seasons_input.lower() == "all":
        return "all"
        
    # If it's a string, try to extract numbers
    if isinstance(seasons_input, str):
        # Extract all numbers from the string
        numbers = re.findall(r'\d+', seasons_input)
        if numbers:
            return [int(num) for num in numbers]
    
    # Default fallback
    return "all"

#####
# Radarr/Sonarr integration (non-Overseerr)
#####

def handle_add_media(hass: HomeAssistant, call: ServiceCall, media_type: str, service_name: str) -> None:
    """Handle the service action to add a media (movie or TV show).

    Args:
        hass (HomeAssistant): The Home Assistant instance.
        call (ServiceCall): The service call object.
        media_type (str): The type of media to add (e.g., "movie" or "series").
        service_name (str): The name of the service (e.g., "radarr" or "sonarr").
    """
    _LOGGER.info(f"Received call data: {call.data}")
    title = call.data.get("title")

    if not title:
        _LOGGER.error("Title is missing in the service call data")
        return

    _LOGGER.info(f"Title received: {title}")

    # Access stored configuration data
    config_data = hass.data[DOMAIN]

    url = config_data.get(f"{service_name}_url")
    api_key = config_data.get(f"{service_name}_api_key")
    quality_profile_id = config_data.get(f"{service_name}_quality_profile_id")

    if not url or not api_key:
        _LOGGER.error(f"{service_name.capitalize()} URL or API key is missing")
        return

    headers = {'X-Api-Key': api_key}

    # Fetch media list
    search_url = urljoin(url, f"api/v3/{media_type}/lookup?term={title}")
    _LOGGER.info(f"Fetching media list from URL: {search_url}")
    media_list = fetch_data(search_url, headers)

    if media_list:
        media_data = media_list[0]

        # Get root folder path
        root_folder_url = urljoin(url, "api/v3/rootfolder")
        root_folder_path = get_root_folder_path(root_folder_url, headers)
        if not root_folder_path:
            return

        # Prepare payload
        payload = {
            'title': media_data['title'],
            'titleSlug': media_data['titleSlug'],
            'images': media_data['images'],
            'year': media_data['year'],
            'rootFolderPath': root_folder_path,
            'addOptions': {
                'searchForMovie' if media_type == 'movie' else 'searchForMissingEpisodes': True
            },
            'qualityProfileId': quality_profile_id,
        }
        
        if media_type == 'movie':
            payload['tmdbId'] = media_data['tmdbId']
        else:  # series/TV show
            payload['tvdbId'] = media_data['tvdbId']
            
            # Set monitored flag for the series itself
            payload['monitored'] = True
            
            # Handle seasons for TV shows if we have season information
            if media_type == 'series' and 'seasons' in media_data:
                # Get the default season preference
                default_season = config_data.get("default_season", "All")
                
                # Process any seasons input from the service call
                raw_seasons_input = call.data.get("seasons", default_season)
                parsed_seasons = parse_seasons_input(raw_seasons_input)
                
                _LOGGER.info(f"Parsed seasons input: {parsed_seasons}")
                
                # Create seasons array for payload
                seasons_array = []
                
                # For Sonarr, we need to explicitly list all seasons with their monitoring status
                for season in media_data.get('seasons', []):
                    season_number = season.get('seasonNumber')
                    
                    # Determine if this season should be monitored
                    is_monitored = False
                    
                    if parsed_seasons == "all":
                        # When "all" is specified, include all seasons EXCEPT season 0 (specials)
                        is_monitored = season_number != 0
                    elif default_season == "Season 1" and parsed_seasons == default_season:
                        # Only monitor season 1
                        is_monitored = season_number == 1
                    elif isinstance(parsed_seasons, list):
                        # Monitor only the specified seasons
                        is_monitored = season_number in parsed_seasons
                    
                    # Add this season to the array
                    seasons_array.append({
                        'seasonNumber': season_number,
                        'monitored': is_monitored
                    })
                
                # Add seasons array to payload
                payload['seasons'] = seasons_array
                
                _LOGGER.info(f"Adding TV show with seasons configuration: {seasons_array}")

        # Add media
        add_url = urljoin(url, f"api/v3/{media_type}")
        _LOGGER.info(f"Adding media to URL: {add_url} with payload: {payload}")
        add_response = requests.post(add_url, json=payload, headers=headers)

        if add_response.status_code == requests.codes.created:
            _LOGGER.info(f"Successfully added {media_type} '{title}' to {service_name.capitalize()}")
        else:
            _LOGGER.error(f"Failed to add {media_type} '{title}' to {service_name.capitalize()}: {add_response.text}")
    else:
        _LOGGER.info(f"No results found for {media_type} '{title}'")

#####
# OVERSEERR Integration
#####

def handle_add_overseerr_media(hass: HomeAssistant, call: ServiceCall, media_type: str) -> None:
    """Handle the service action to add a media (movie or TV show) using Overseerr."""
    _LOGGER.info(f"Received call data: {call.data}")
    title = call.data.get("title")
    
    if not title:
        _LOGGER.error("Title is missing in the service call data")
        return
        
    # Check for duplicate requests within 10 seconds
    request_key = f"{title}:{media_type}"
    current_time = datetime.now()
    
    if request_key in _RECENT_REQUESTS:
        last_request_time = _RECENT_REQUESTS[request_key]
        time_diff = (current_time - last_request_time).total_seconds()
        
        if time_diff < 10:  # Within 10 seconds
            _LOGGER.warning(f"Duplicate request for '{title}' detected within {time_diff:.2f} seconds. Ignoring.")
            return
    
    # Update the request tracker
    _RECENT_REQUESTS[request_key] = current_time
    
    # Clean up old entries (older than 2 minutes)
    cleanup_time = current_time - timedelta(minutes=2)
    for key in list(_RECENT_REQUESTS.keys()):
        if _RECENT_REQUESTS[key] < cleanup_time:
            del _RECENT_REQUESTS[key]
    
    _LOGGER.info(f"Processing request for title: {title}")
    
    # Access stored configuration data
    config_data = hass.data[DOMAIN]
    
    # Get seasons from call data, or use the configured default
    default_season = config_data.get("default_season", "All")
    if default_season == "Season 1":
        default_seasons = [1]
    else:  # "All"
        default_seasons = "all"

    # Get and parse the seasons input
    raw_seasons_input = call.data.get("seasons", default_seasons)
    parsed_seasons = parse_seasons_input(raw_seasons_input)
    _LOGGER.info(f"Parsed seasons input for Overseerr: {parsed_seasons}")

    # Format seasons for Overseerr payload
    if parsed_seasons == "all":
        # For "all", Overseerr expects the string "all"
        seasons_to_use = "all"
    elif isinstance(parsed_seasons, list):
        # For specific seasons, use the list of integers
        seasons_to_use = parsed_seasons
    else:
        # Fallback
        seasons_to_use = "all"

    # Access stored configuration data
    url = config_data.get("overseerr_url")
    api_key = config_data.get("overseerr_api_key")

    if not url or not api_key:
        _LOGGER.error("Overseerr URL or API key is missing")
        return

    # Ensure the URL has a scheme
    parsed_url = urlparse(url)
    if not parsed_url.scheme:
        url_https = f"https://{url}"
        url_http = f"http://{url}"
    else:
        url_https = url
        url_http = url

    headers = {'X-Api-Key': api_key}

    # Try https first
    search_url = urljoin(url_https, f"api/v1/search?query={title}")
    _LOGGER.info(f"Searching for media with URL: {search_url}")
    search_results = fetch_data(search_url, headers)

    if not search_results or not search_results.get("results"):
        # Try with http if https fails
        search_url = urljoin(url_http, f"api/v1/search?query={title}")
        _LOGGER.error(f"Retrying search for media with URL: {search_url}")
        _LOGGER.info(f"Retrying search for media with URL: {search_url}")
        search_results = fetch_data(search_url, headers)

    if search_results and search_results.get("results"):
        media_data = search_results["results"][0]
        _LOGGER.info(f"Media data: {media_data}")

        # Prepare payload
        payload = {
            "mediaType": media_type,
            "mediaId": media_data["id"],
            "is4k": False,
            "userId": config_data.get("overseerr_user_id"),
            "seasons": seasons_to_use if media_type == "tv" else []
        }
        
        # Add server and profile information based on media type
        if media_type == "movie":
            # Add Radarr server and profile if configured
            if config_data.get("radarr_server_id"):
                payload["serverId"] = config_data.get("radarr_server_id")
                
                # Add profile if available
                if config_data.get("radarr_profile_id"):
                    payload["profileId"] = config_data.get("radarr_profile_id")
        elif media_type == "tv":
            # Add Sonarr server and profile if configured
            if config_data.get("sonarr_server_id"):
                payload["serverId"] = config_data.get("sonarr_server_id")
                
                # Add profile if available
                if config_data.get("sonarr_profile_id"):
                    payload["profileId"] = config_data.get("sonarr_profile_id")
                    
            # Add tvdbId if available
            tvdb_id = media_data.get("tvdbId")
            if tvdb_id is not None:
                payload["tvdbId"] = tvdb_id

        # Create request
        request_url = urljoin(url_https, "api/v1/request")
        _LOGGER.info(f"Creating request with URL: {request_url} and payload: {payload}")

        request_response = requests.post(request_url, json=payload, headers=headers)

        if request_response.status_code == requests.codes.created:
            _LOGGER.info(f"Successfully created request for {media_type} '{title}' in Overseerr")
        else:
            _LOGGER.error(f"Failed to create request for {media_type} '{title}' in Overseerr: {request_response.text}")
    else:
        _LOGGER.info(f"No results found for {media_type} '{title}'")