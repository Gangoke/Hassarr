from __future__ import annotations

from typing import Any, Optional, Iterable, Type
import asyncio
import logging

from aiohttp import ClientSession, ClientTimeout, ClientError
from yarl import URL

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
RETRY_STATUSES = {502, 503, 504}


class ApiError(Exception):
    """Base API error."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OverseerrError(ApiError):
    pass


class ArrError(ApiError):
    pass


class _BaseClient:
    ERR_CLS: Type[ApiError] = ApiError

    def __init__(self, base_url: str, api_key: str, session: ClientSession, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._base = URL(base_url.rstrip("/"))
        self._session = session
        self._headers = {
            "X-Api-Key": api_key.strip(),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Hassarr/0.6 (+https://github.com/Gangoke/Hassarr)",
        }
        self._timeout = ClientTimeout(total=timeout)

    async def _request(self, method: str, path: str, *, json: Any | None = None, retry: int = 2, **kwargs) -> Any:
        url = self._base.join(URL(path.lstrip("/")))
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._session.request(method, url, headers=self._headers, json=json, timeout=self._timeout, **kwargs) as resp:
                    status = resp.status
                    if status >= 400:
                        text = await resp.text()
                        if attempt <= retry and (status in RETRY_STATUSES):
                            await asyncio.sleep(0.5 * attempt)
                            continue
                        raise self.ERR_CLS(f"{method} {url} -> {status}: {text[:300]}", status=status)
                    ct = resp.headers.get("Content-Type", "")
                    if "application/json" in ct:
                        return await resp.json()
                    return await resp.text()
            except (asyncio.TimeoutError, ClientError) as e:
                if attempt <= retry:
                    _LOGGER.debug("Transient error on %s %s (%s), retry %s/%s", method, url, e, attempt, retry)
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise self.ERR_CLS(f"{method} {url} failure: {e}") from e


class OverseerrClient(_BaseClient):
    ERR_CLS = OverseerrError

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/v1/status")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def list_radarr(self) -> list[dict]:
        return await self._request("GET", "/api/v1/service/radarr")

    async def list_sonarr(self) -> list[dict]:
        return await self._request("GET", "/api/v1/service/sonarr")

    async def get_radarr_details(self, radarr_id: int) -> dict:
        return await self._request("GET", f"/api/v1/service/radarr/{radarr_id}")

    async def get_sonarr_details(self, sonarr_id: int) -> dict:
        return await self._request("GET", f"/api/v1/service/sonarr/{sonarr_id}")

    async def search(self, query: str) -> list[dict]:
        from yarl import URL as _URL
        qs = _URL.build(query=query).query_string.replace("query=", "")
        j = await self._request("GET", f"/api/v1/search?query={qs}")
        if isinstance(j, dict) and "results" in j:
            return j.get("results") or []
        if isinstance(j, list):
            return j
        return []

    @staticmethod
    def _norm_type(media_type: str) -> str:
        mt = (media_type or "").strip().lower()
        if mt == "show":
            return "tv"
        if mt in ("movie", "tv"):
            return mt
        raise OverseerrError("media_type must be 'movie' or 'tv' (or 'show').")

    @staticmethod
    def _best_match(results: list[dict], media_type: str) -> Optional[dict]:
        mt = media_type.lower()
        typed = [r for r in results if (r.get("mediaType") or r.get("media_type")) == mt]
        pool = typed or results

        def score(r: dict) -> float:
            s = r.get("popularity") or r.get("voteAverage") or r.get("vote_average") or 0.0
            return float(s)

        return max(pool, key=score, default=None)

    async def request_media(
        self,
        query: str,
        media_type: str,
        seasons: Optional[str | Iterable[int]] = None,
        is_4k: bool = False,
        server_id: Optional[int] = None,
        profile_id: Optional[int] = None,
    ) -> dict:
        media_type = self._norm_type(media_type)
        results = await self.search(query)
        if not results:
            raise OverseerrError(f"No results for '{query}'")

        best = self._best_match(results, media_type)
        if not best:
            raise OverseerrError(f"No suitable '{media_type}' result for '{query}'")

        tmdb_id = best.get("id") or best.get("tmdbId") or best.get("tmdb_id")
        if not tmdb_id:
            raise OverseerrError("Best match is missing a TMDB id.")

        payload: dict[str, Any] = {
            "mediaType": media_type,
            "mediaId": int(tmdb_id),
            "is4k": bool(is_4k),
        }
        if server_id is not None:
            payload["serverId"] = int(server_id)
        if profile_id is not None:
            payload["profileId"] = int(profile_id)
        if media_type == "tv":
            if isinstance(seasons, str) and seasons.strip().lower() == "all":
                payload["seasons"] = "all"
            elif seasons:
                payload["seasons"] = [int(x) for x in seasons]
        return await self._request("POST", "/api/v1/request", json=payload)


class _BaseArr(_BaseClient):
    pass


class RadarrClient(_BaseArr):
    ERR_CLS = ArrError

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/v3/system/status")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def lookup(self, query: str) -> list[dict]:
        from yarl import URL as _URL
        qs = _URL.build(term=query).query_string
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
    ERR_CLS = ArrError

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/v3/system/status")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def lookup(self, query: str) -> list[dict]:
        from yarl import URL as _URL
        qs = _URL.build(term=query).query_string
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
