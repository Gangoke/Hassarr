from __future__ import annotations

from typing import Any, Iterable, Optional
from aiohttp import ClientSession, ClientTimeout
from yarl import URL


class OverseerrError(Exception):
    pass


class OverseerrClient:
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
                raise OverseerrError(f"{method} {url} -> {resp.status}: {text}")
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct:
                return await resp.json()
            return await resp.text()

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/api/v1/status")
            return True
        except Exception:
            return False

    # Service discovery
    async def list_radarr(self) -> list[dict]:
        return await self._request("GET", "/api/v1/service/radarr")

    async def list_sonarr(self) -> list[dict]:
        return await self._request("GET", "/api/v1/service/sonarr")

    async def get_radarr_details(self, radarr_id: int) -> dict:
        return await self._request("GET", f"/api/v1/service/radarr/{radarr_id}")

    async def get_sonarr_details(self, sonarr_id: int) -> dict:
        return await self._request("GET", f"/api/v1/service/sonarr/{sonarr_id}")

    # Search + request
    async def search(self, query: str) -> list[dict]:
        qs = URL.build(query=query).query_string.replace("query=", "")
        j = await self._request("GET", f"/api/v1/search?query={qs}")
        if isinstance(j, dict) and "results" in j:
            return j["results"] or []
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
            return float(r.get("popularity") or r.get("voteAverage") or r.get("vote_average") or 0.0)

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
            "mediaType": media_type,  # "movie" or "tv"
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
