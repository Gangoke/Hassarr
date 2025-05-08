import logging
from urllib.parse import urljoin
import voluptuous as vol
from homeassistant import config_entries
import aiohttp
from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN

class HassarrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial step for user configuration."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("integration_type"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["Radarr & Sonarr", "Overseerr"],
                            translation_key="integration_type"
                        )
                    )
                })
            )

        self.integration_type = user_input["integration_type"]
        if self.integration_type == "Radarr & Sonarr":
            return await self.async_step_radarr_sonarr()
        else:
            return await self.async_step_overseerr()
    
    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration of an existing entry."""
        if user_input is not None:
            self.integration_type = user_input["integration_type"]
            if self.integration_type == "Radarr & Sonarr":
                return await self.async_step_reconfigure_radarr_sonarr()
            else:
                return await self.async_step_reconfigure_overseerr()

        # Get existing data to pre-fill the form
        existing_data = self._get_reconfigure_entry().data
        integration_type = existing_data.get("integration_type", "Radarr & Sonarr")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required("integration_type", default=integration_type): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["Radarr & Sonarr", "Overseerr"],
                        translation_key="integration_type"
                    )
                )
            })
        )

#####
# Radarr/Sonarr configuration flow (Non-Overseerr)
#####

    async def async_step_radarr_sonarr(self, user_input=None):
        """Configure Radarr & Sonarr integration."""
        errors = {}
        
        if user_input is not None:
            # Validate user input
            if not user_input.get("radarr_url") or not user_input.get("radarr_api_key"):
                errors["base"] = "missing_radarr_info"
            if not user_input.get("sonarr_url") or not user_input.get("sonarr_api_key"):
                errors["base"] = "missing_sonarr_info"

            if not errors:
                # Save the connection details and proceed to quality profile selection
                self.radarr_url = user_input["radarr_url"]
                self.radarr_api_key = user_input["radarr_api_key"]
                self.sonarr_url = user_input["sonarr_url"]
                self.sonarr_api_key = user_input["sonarr_api_key"]
                return await self.async_step_radarr_sonarr_quality_profiles()

        return self.async_show_form(
            step_id="radarr_sonarr", 
            data_schema=self._get_radarr_sonarr_schema(),
            errors=errors
        )

    async def async_step_radarr_sonarr_quality_profiles(self, user_input=None):
        """Configure quality profiles for Radarr & Sonarr."""
        if user_input is None:
            # Fetch quality profiles from Radarr and Sonarr APIs
            radarr_profiles = await self._fetch_quality_profiles(self.radarr_url, self.radarr_api_key)
            sonarr_profiles = await self._fetch_quality_profiles(self.sonarr_url, self.sonarr_api_key)

            radarr_options = {profile["id"]: profile["name"] for profile in radarr_profiles}
            sonarr_options = {profile["id"]: profile["name"] for profile in sonarr_profiles}

            return self.async_show_form(
                step_id="radarr_sonarr_quality_profiles",
                data_schema=vol.Schema({
                    vol.Required("radarr_quality_profile_id"): vol.In(radarr_options),
                    vol.Required("sonarr_quality_profile_id"): vol.In(sonarr_options),
                    vol.Required("default_season_behavior", default="All"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["All", "Season 1"],
                            translation_key="default_season_behavior"
                        )
                    )
                })
            )

        # Create the entry with all the collected data
        user_input.update({
            "radarr_url": self.radarr_url,
            "radarr_api_key": self.radarr_api_key,
            "sonarr_url": self.sonarr_url,
            "sonarr_api_key": self.sonarr_api_key,
            "integration_type": "Radarr & Sonarr",
            # Use a consistent key name between integration types
            "default_season": user_input["default_season_behavior"] 
        })
        return self.async_create_entry(title="Hassarr", data=user_input)
        
    async def async_step_reconfigure_radarr_sonarr(self, user_input=None):
        """Handle reconfiguration for Radarr & Sonarr."""
        if user_input is not None:
            # Update the existing config entry
            data = dict(self._get_reconfigure_entry().data)
            data.update(user_input)
            self.hass.config_entries.async_update_entry(
                self._get_reconfigure_entry(),
                data=data
            )
            return await self.async_step_reconfigure_radarr_sonarr_quality_profiles()

        # Get existing data to pre-fill the form
        existing_data = self._get_reconfigure_entry().data

        return self.async_show_form(
            step_id="reconfigure_radarr_sonarr",
            data_schema=vol.Schema({
                vol.Optional("radarr_url", default=existing_data.get("radarr_url", "")): str,
                vol.Optional("sonarr_url", default=existing_data.get("sonarr_url", "")): str,
                vol.Optional("radarr_api_key", default=existing_data.get("radarr_api_key", "")): str,
                vol.Optional("sonarr_api_key", default=existing_data.get("sonarr_api_key", "")): str,
            })
        )

    async def async_step_reconfigure_radarr_sonarr_quality_profiles(self, user_input=None):
        """Handle reconfiguration for Radarr & Sonarr quality profiles."""
        if user_input is not None:
            # Update the existing config entry
            data = dict(self._get_reconfigure_entry().data)
            data.update(user_input)
            
            # Make sure we use the consistent key name
            if "default_season_behavior" in user_input:
                data["default_season"] = user_input["default_season_behavior"]
                
            self.hass.config_entries.async_update_entry(
                self._get_reconfigure_entry(),
                data=data
            )
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=data,
            )

        # Get existing data to pre-fill the form
        existing_data = self._get_reconfigure_entry().data
        radarr_url = existing_data.get("radarr_url")
        radarr_api_key = existing_data.get("radarr_api_key")
        sonarr_url = existing_data.get("sonarr_url")
        sonarr_api_key = existing_data.get("sonarr_api_key")
        default_season = existing_data.get("default_season", "All")

        # Fetch quality profiles from Radarr and Sonarr APIs
        radarr_profiles = await self._fetch_quality_profiles(radarr_url, radarr_api_key)
        sonarr_profiles = await self._fetch_quality_profiles(sonarr_url, sonarr_api_key)

        radarr_options = {profile["id"]: profile["name"] for profile in radarr_profiles}
        sonarr_options = {profile["id"]: profile["name"] for profile in sonarr_profiles}

        return self.async_show_form(
            step_id="reconfigure_radarr_sonarr_quality_profiles",
            data_schema=vol.Schema({
                vol.Required("radarr_quality_profile_id"): vol.In(radarr_options),
                vol.Required("sonarr_quality_profile_id"): vol.In(sonarr_options),
                vol.Required("default_season_behavior", default=default_season): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["All", "Season 1"],
                        translation_key="default_season_behavior"
                    )
                )
            })
        )

    async def _fetch_quality_profiles(self, url, api_key):
        """Fetch quality profiles from the Radarr/Sonarr API."""
        async with aiohttp.ClientSession() as session:
            api_url = urljoin(url, "api/v3/qualityprofile")
            try:
                async with session.get(api_url, headers={"X-Api-Key": api_key}) as response:
                    response.raise_for_status()
                    return await response.json()
            except aiohttp.ClientError as error:
                _LOGGER.error(f"Error fetching quality profiles: {error}")
                return []

    @staticmethod
    def _get_radarr_sonarr_schema():
        """Get schema for Radarr & Sonarr configuration."""
        return vol.Schema({
            vol.Required("radarr_url"): str,
            vol.Required("radarr_api_key"): str,
            vol.Required("sonarr_url"): str,
            vol.Required("sonarr_api_key"): str,
        })

#####
# OVERSEERR Configuration Flow
#####

    async def async_step_overseerr(self, user_input=None):
        """Configure Overseerr integration."""
        errors = {}
        
        if user_input is not None:
            # Validate user input
            if not user_input.get("overseerr_url") or not user_input.get("overseerr_api_key"):
                errors["base"] = "missing_overseerr_info"
            
            if not errors:
                # Save the connection details and proceed to user selection
                if not user_input["overseerr_url"].startswith(("http://", "https://")):
                    self.overseerr_url = f"http://{user_input['overseerr_url']}"
                else:
                    self.overseerr_url = user_input["overseerr_url"]
                self.overseerr_api_key = user_input["overseerr_api_key"]
                return await self.async_step_overseerr_user()

        return self.async_show_form(
            step_id="overseerr", 
            data_schema=self._get_overseerr_schema(), 
            errors=errors
        )

    async def async_step_overseerr_user(self, user_input=None):
        """Select Overseerr user."""
        if user_input is None:
            # Fetch users from Overseerr API
            users = await self._fetch_overseerr_users(self.overseerr_url, self.overseerr_api_key)
            user_options = {user["id"]: user["username"] for user in users}

            return self.async_show_form(
                step_id="overseerr_user",
                data_schema=vol.Schema({
                    vol.Required("overseerr_user_id"): vol.In(user_options),
                })
            )

        # Save the user selection and proceed to the combined server and defaults selection
        self.overseerr_user_id = user_input["overseerr_user_id"]
        return await self.async_step_overseerr_servers_and_defaults()

    async def async_step_overseerr_servers_and_defaults(self, user_input=None):
        """Select Radarr/Sonarr servers and default behavior in one step."""
        if user_input is None:
            # Fetch Radarr servers from Overseerr API
            radarr_servers = await self._fetch_overseerr_servers(
                self.overseerr_url, self.overseerr_api_key, "radarr"
            )
            radarr_options = {server["id"]: server["name"] for server in radarr_servers}
            self.radarr_servers = {server["id"]: server for server in radarr_servers}
            
            # Fetch Sonarr servers from Overseerr API
            sonarr_servers = await self._fetch_overseerr_servers(
                self.overseerr_url, self.overseerr_api_key, "sonarr"
            )
            sonarr_options = {server["id"]: server["name"] for server in sonarr_servers}
            self.sonarr_servers = {server["id"]: server for server in sonarr_servers}
            
            # Create schema
            schema = {}
            
            # Add Radarr server selection if available
            if radarr_options:
                schema[vol.Optional("radarr_server_id")] = vol.In(radarr_options)
            
            # Add Sonarr server selection if available
            if sonarr_options:
                schema[vol.Optional("sonarr_server_id")] = vol.In(sonarr_options)
                
            # Always add default season behavior with selector for translation
            schema[vol.Required("default_season_behavior", default="All")] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["All", "Season 1"],
                    translation_key="default_season_behavior"
                )
            )
            
            return self.async_show_form(
                step_id="overseerr_servers_and_defaults",
                data_schema=vol.Schema(schema)
            )

        # Save the selected options
        if "radarr_server_id" in user_input and user_input["radarr_server_id"]:
            self.radarr_server_id = int(user_input["radarr_server_id"]) 
            selected_radarr = self.radarr_servers[self.radarr_server_id]
            self.radarr_profile_id = selected_radarr.get("activeProfileId")
        else:
            self.radarr_server_id = None
            
        if "sonarr_server_id" in user_input and user_input["sonarr_server_id"]:
            self.sonarr_server_id = int(user_input["sonarr_server_id"])
            selected_sonarr = self.sonarr_servers[self.sonarr_server_id]
            self.sonarr_profile_id = selected_sonarr.get("activeProfileId")
        else:
            self.sonarr_server_id = None
        
        # Create the entry with all the collected data
        data = {
            "overseerr_url": self.overseerr_url,
            "overseerr_api_key": self.overseerr_api_key,
            "overseerr_user_id": self.overseerr_user_id,
            "default_season": user_input["default_season_behavior"],
            "integration_type": "Overseerr"
        }

        # Add Radarr server and profile if selected
        if hasattr(self, 'radarr_server_id') and self.radarr_server_id is not None:
            data["radarr_server_id"] = self.radarr_server_id
            if hasattr(self, 'radarr_profile_id'):
                data["radarr_profile_id"] = self.radarr_profile_id

        # Add Sonarr server and profile if selected
        if hasattr(self, 'sonarr_server_id') and self.sonarr_server_id is not None:
            data["sonarr_server_id"] = self.sonarr_server_id
            if hasattr(self, 'sonarr_profile_id'):
                data["sonarr_profile_id"] = self.sonarr_profile_id

        return self.async_create_entry(title="Hassarr", data=data)

    async def async_step_reconfigure_overseerr(self, user_input=None):
        """Handle reconfiguration for Overseerr."""
        if user_input is not None:
            # Update the existing config entry
            data = dict(self._get_reconfigure_entry().data)
            data.update(user_input)
            self.hass.config_entries.async_update_entry(
                self._get_reconfigure_entry(),
                data=data
            )
            return await self.async_step_reconfigure_overseerr_user()

        # Get existing data to pre-fill the form
        existing_data = self._get_reconfigure_entry().data

        return self.async_show_form(
            step_id="reconfigure_overseerr",
            data_schema=vol.Schema({
                vol.Optional("overseerr_url", default=existing_data.get("overseerr_url", "")): str,
                vol.Optional("overseerr_api_key", default=existing_data.get("overseerr_api_key", "")): str,
            })
        )

    async def async_step_reconfigure_overseerr_user(self, user_input=None):
        """Handle reconfiguration for Overseerr user selection."""
        if user_input is not None:
            # Update the existing config entry
            data = dict(self._get_reconfigure_entry().data)
            data.update(user_input)
            self.hass.config_entries.async_update_entry(
                self._get_reconfigure_entry(),
                data=data
            )
            return await self.async_step_reconfigure_overseerr_servers_and_defaults()

        # Get existing data to pre-fill the form
        existing_data = self._get_reconfigure_entry().data
        overseerr_url = existing_data.get("overseerr_url")
        overseerr_api_key = existing_data.get("overseerr_api_key")

        # Fetch users from Overseerr API
        users = await self._fetch_overseerr_users(overseerr_url, overseerr_api_key)
        user_options = {user["id"]: user["username"] for user in users}

        return self.async_show_form(
            step_id="reconfigure_overseerr_user",
            data_schema=vol.Schema({
                vol.Required("overseerr_user_id", default=existing_data.get("overseerr_user_id")): vol.In(user_options),
            })
        )

    async def async_step_reconfigure_overseerr_servers_and_defaults(self, user_input=None):
        """Handle reconfiguration for servers and defaults in one step."""
        if user_input is not None:
            # Update the existing config entry
            data = dict(self._get_reconfigure_entry().data)
            
            # Update with the new selections
            if "radarr_server_id" in user_input:
                # Convert to integer if it's a string ID, or set to None if empty
                if user_input["radarr_server_id"]:
                    data["radarr_server_id"] = int(user_input["radarr_server_id"]) 
                    # Get the active profile ID for the selected Radarr server
                    radarr_servers = await self._fetch_overseerr_servers(
                        data.get("overseerr_url"), 
                        data.get("overseerr_api_key"), 
                        "radarr"
                    )
                    for server in radarr_servers:
                        if server["id"] == int(user_input["radarr_server_id"]):
                            data["radarr_profile_id"] = server.get("activeProfileId")
                            break
                else:
                    data["radarr_server_id"] = None
                    # If server deselected, remove profile too
                    if "radarr_profile_id" in data:
                        del data["radarr_profile_id"]
                        
            if "sonarr_server_id" in user_input:
                # Convert to integer if it's a string ID, or set to None if empty
                if user_input["sonarr_server_id"]:
                    data["sonarr_server_id"] = int(user_input["sonarr_server_id"])
                    # Get the active profile ID for the selected Sonarr server
                    sonarr_servers = await self._fetch_overseerr_servers(
                        data.get("overseerr_url"), 
                        data.get("overseerr_api_key"), 
                        "sonarr"
                    )
                    for server in sonarr_servers:
                        if server["id"] == int(user_input["sonarr_server_id"]):
                            data["sonarr_profile_id"] = server.get("activeProfileId")
                            break
                else:
                    data["sonarr_server_id"] = None
                    # If server deselected, remove profile too
                    if "sonarr_profile_id" in data:
                        del data["sonarr_profile_id"]
            
            # Update default season behavior
            data["default_season"] = user_input.get("default_season_behavior", "All")
            
            self.hass.config_entries.async_update_entry(
                self._get_reconfigure_entry(),
                data=data
            )
                        
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=data,
            )

        # Get existing data
        existing_data = self._get_reconfigure_entry().data
        overseerr_url = existing_data.get("overseerr_url")
        overseerr_api_key = existing_data.get("overseerr_api_key")

        # Fetch Radarr servers from Overseerr API
        radarr_servers = await self._fetch_overseerr_servers(overseerr_url, overseerr_api_key, "radarr")
        radarr_options = {server["id"]: server["name"] for server in radarr_servers}
        
        # Fetch Sonarr servers from Overseerr API
        sonarr_servers = await self._fetch_overseerr_servers(overseerr_url, overseerr_api_key, "sonarr")
        sonarr_options = {server["id"]: server["name"] for server in sonarr_servers}
        
        # Create schema
        schema = {}
        
        # Add Radarr server selection if available
        if radarr_options:
            schema[vol.Optional("radarr_server_id", default=existing_data.get("radarr_server_id"))] = vol.In(radarr_options)
        
        # Add Sonarr server selection if available
        if sonarr_options:
            schema[vol.Optional("sonarr_server_id", default=existing_data.get("sonarr_server_id"))] = vol.In(sonarr_options)
            
        # Always add default season behavior
        schema[vol.Required("default_season_behavior", default=existing_data.get("default_season", "All"))] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["All", "Season 1"],
                translation_key="default_season_behavior"
            )
        )
        
        return self.async_show_form(
            step_id="reconfigure_overseerr_servers_and_defaults",
            data_schema=vol.Schema(schema)
        )

    async def _fetch_overseerr_users(self, url, api_key):
        """Fetch users from the Overseerr API."""
        async with aiohttp.ClientSession() as session:
            api_url = urljoin(url, "api/v1/user")
            try:
                async with session.get(api_url, headers={"X-Api-Key": api_key}) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data["results"]
            except aiohttp.ClientError as error:
                _LOGGER.error(f"Error fetching Overseerr users: {error}")
                return []

    async def _fetch_overseerr_servers(self, url, api_key, server_type):
        """Fetch Radarr or Sonarr servers from the Overseerr API."""
        async with aiohttp.ClientSession() as session:
            api_url = urljoin(url, f"api/v1/settings/{server_type}")
            try:
                _LOGGER.debug(f"Fetching {server_type} servers from Overseerr: {api_url}")
                async with session.get(api_url, headers={"X-Api-Key": api_key}) as response:
                    response.raise_for_status()
                    data = await response.json()
                    _LOGGER.debug(f"Received {server_type} server data: {data}")
                    # The API returns a direct array of servers, not an object with a servers property
                    if isinstance(data, list):
                        return data
                    # Fallback to previous behavior just in case
                    return data.get("servers", [])
            except aiohttp.ClientError as error:
                _LOGGER.error(f"Error fetching Overseerr {server_type} servers: {error}")
                return []
            except Exception as error:
                _LOGGER.error(f"Unexpected error fetching {server_type} servers: {error}")
                return []

    async def _fetch_overseerr_profiles(self, url, api_key, server_type, server_id):
        """Fetch quality profiles for a specific server from Overseerr API."""
        async with aiohttp.ClientSession() as session:
            api_url = urljoin(url, f"api/v1/settings/{server_type}/{server_id}/profiles")
            try:
                async with session.get(api_url, headers={"X-Api-Key": api_key}) as response:
                    response.raise_for_status()
                    return await response.json()
            except aiohttp.ClientError as error:
                _LOGGER.error(f"Error fetching {server_type} profiles: {error}")
                return []

    @staticmethod
    def _get_overseerr_schema():
        """Get schema for Overseerr configuration."""
        return vol.Schema({
            vol.Required("overseerr_url"): str,
            vol.Required("overseerr_api_key"): str
        })

#####
# COMMON METHODS
#####

    def _get_reconfigure_entry(self):
        """Get the config entry being reconfigured."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])