from __future__ import annotations

from typing import Any, Iterable, Optional
from aiohttp import ClientSession, ClientTimeout
from yarl import URL


class ArrError(Exception):
    pass


class _BaseArr:
    def __init__(self, base_url: str, api_key: str, session: ClientSession) -> None:
        self._base = URL(base_url.rstrip("/"))
        self._session = session
        self._headers = {
            "X-Api-Key": api_key.strip(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._timeout = ClientTimeout(total=20)

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        url = self._base.with_path(str(self._base.path) + path)
        async with self._session.request(method, url, headers=self._headers, timeout=self._timeout, **kwargs) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise ArrError(f"{method} {url} -> {resp.status}: {text}")
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct:
                return await resp.json()
            return await resp.text()


class RadarrClient(_BaseArr):
    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/v3/system/status")
            return True
        except Exception:
            return False

    async def lookup(self, query: str) -> list[dict]:
        qs = URL.build(term=query).query_string
        return await self._request("GET", f"/api/v3/movie/lookup?{qs}")

    async def add_movie(self, tmdb_id: int, root: str, profile_id: int) -> dict:
        items = await self.lookup(f"tmdb:{tmdb_id}")
        if not items:
            raise ArrError(f"Radarr lookup failed for tmdb:{tmdb_id}")
        m = items[0]
        payload = {
            "tmdbId": tmdb_id,
            "title": m.get("title"),
            "year": m.get("year"),
            "titleSlug": m.get("titleSlug"),
            "qualityProfileId": int(profile_id),
            "monitored": True,
            "rootFolderPath": root,
            "addOptions": {"searchForMovie": True},
            "images": m.get("images", []),
        }
        return await self._request("POST", "/api/v3/movie", json=payload)


class SonarrClient(_BaseArr):
    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/v3/system/status")
            return True
        except Exception:
            return False

    async def lookup(self, query: str) -> list[dict]:
        qs = URL.build(term=query).query_string
        return await self._request("GET", f"/api/v3/series/lookup?{qs}")

    async def add_series(
        self,
        tmdb_id: int,
        root: str,
        quality_profile_id: int,
        language_profile_id: Optional[int] = None,
        seasons: Optional[str | Iterable[int]] = None,
    ) -> dict:
        items = await self.lookup(f"tmdb:{tmdb_id}")
        if not items:
            raise ArrError(f"Sonarr lookup failed for tmdb:{tmdb_id}")
        s = items[0]

        monitored_set = None
        if isinstance(seasons, str) and seasons.strip().lower() == "all":
            monitored_set = {int(sea.get("seasonNumber")) for sea in s.get("seasons", [])}
        elif seasons:
            monitored_set = {int(x) for x in seasons}

        payload = {
            "title": s.get("title"),
            "titleSlug": s.get("titleSlug"),
            "images": s.get("images", []),
            "seasons": [
                {
                    "seasonNumber": int(sea.get("seasonNumber")),
                    "monitored": (monitored_set is None) or (int(sea.get("seasonNumber")) in monitored_set),
                }
                for sea in s.get("seasons", [])
            ],
            "rootFolderPath": root,
            "qualityProfileId": int(quality_profile_id),
            "languageProfileId": int(language_profile_id) if language_profile_id else None,
            "monitored": True,
            "addOptions": {"searchForMissingEpisodes": True},
            "tmdbId": int(tmdb_id),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self._request("POST", "/api/v3/series", json=payload)
