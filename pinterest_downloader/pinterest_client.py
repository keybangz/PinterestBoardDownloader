import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from .config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pin:
    """Represents a Pinterest pin with media information."""
    
    id: str
    title: str
    description: str
    media_url: str
    media_type: str
    original_filename: Optional[str]
    board_id: str
    created_at: str
    
    @property
    def filename(self) -> str:
        """Generate filename from original or title."""
        if self.original_filename:
            return self._sanitize_filename(self.original_filename)
        
        safe_title = self._sanitize_filename(self.title or self.id)
        extension = self._get_extension()
        return f"{safe_title}.{extension}"
    
    def _sanitize_filename(self, name: str) -> str:
        """Remove or replace invalid filename characters."""
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
        safe = re.sub(r'_+', '_', safe).strip('_')
        return safe[:200] if safe else self.id
    
    def _get_extension(self) -> str:
        """Extract file extension from media URL."""
        parsed = urlparse(self.media_url)
        path = parsed.path.lower()
        
        for ext in ['.jpg', '.jpeg']:
            if ext in path:
                return 'jpg'
        for ext, result in [('.png', 'png'), ('.gif', 'gif'), ('.mp4', 'mp4'), ('.webp', 'webp'), ('.mov', 'mov')]:
            if ext in path:
                return result
        return 'jpg'


@dataclass(frozen=True)
class Board:
    """Represents a Pinterest board."""
    
    id: str
    name: str
    description: str
    pin_count: int
    owner_id: str
    url: Optional[str] = None


class RateLimiter:
    """Token bucket rate limiter for API calls."""
    
    def __init__(self, min_interval: float = 0.1) -> None:
        self.min_interval = min_interval
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Wait until rate limit allows next call."""
        async with self._lock:
            now = time.monotonic()
            wait_time = self._last_call + self.min_interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_call = time.monotonic()


class PinterestClient:
    """Client for interacting with Pinterest API."""
    
    API_BASE_URL = "https://api.pinterest.com/v5"
    
    def __init__(self, config: Config) -> None:
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(min_interval=config.rate_limit_delay)
        self._closed: bool = False
    
    async def __aenter__(self) -> "PinterestClient":
        if self._closed:
            raise RuntimeError("Client has already been closed")
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create aiohttp session with optimized settings."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.request_timeout,
                connect=self.config.connect_timeout,
                sock_read=self.config.request_timeout
            )
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=10,
                ttl_dns_cache=300,
                force_close=False,
                ssl=self.config.verify_ssl
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                raise_for_status=False,
                json_serialize=json.dumps,
                headers={"User-Agent": self.config.user_agent}
            )
        return self._session
    
    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._closed = True
    
    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        """Make authenticated request to Pinterest API with rate limiting."""
        if self._closed:
            raise RuntimeError("Client has been closed")
        
        session = await self._ensure_session()
        url = f"{self.API_BASE_URL}{endpoint}"
        headers = self._get_headers()
        headers.update(kwargs.pop("headers", {}))
        
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.config.max_retries):
            try:
                await self._rate_limiter.acquire()
                
                async with session.request(method, url, headers=headers, **kwargs) as response:
                    if response.status == 200:
                        try:
                            return await response.json()
                        except (json.JSONDecodeError, aiohttp.ContentTypeError) as e:
                            text = await response.text()
                            logger.debug(f"Invalid JSON from {endpoint}: {text[:500]}")
                            raise PinterestAPIError(f"Invalid JSON response from {endpoint}: {e}")
                    
                    elif response.status == 401:
                        raise PinterestAuthError(f"Authentication failed for {endpoint}. Check your access token.")
                    
                    elif response.status == 429:
                        try:
                            retry_after = int(response.headers.get("Retry-After", "60"))
                        except ValueError:
                            retry_after = 60
                        jitter = random.uniform(0, 5)
                        logger.warning(f"Rate limited on {endpoint}, waiting {retry_after + jitter:.1f}s")
                        await asyncio.sleep(retry_after + jitter)
                        continue
                    
                    elif response.status >= 500:
                        base_wait = min(2 ** attempt, 30)
                        jitter = random.uniform(0, base_wait * 0.1)
                        logger.warning(f"Server error {response.status} from {endpoint}, retrying in {base_wait + jitter:.1f}s")
                        await asyncio.sleep(base_wait + jitter)
                        continue
                    
                    elif response.status == 404:
                        raise PinterestNotFoundError(f"Resource not found: {endpoint}")
                    
                    elif response.status == 403:
                        raise PinterestAuthError(f"Access forbidden for {endpoint}. Check permissions.")
                    
                    else:
                        try:
                            error_data = await response.json()
                            message = error_data.get("message", str(response.status))
                        except Exception:
                            message = str(response.status)
                        raise PinterestAPIError(f"API error ({response.status}): {message}")
                        
            except aiohttp.ClientError as e:
                last_exception = e
                base_wait = min(2 ** attempt, 30)
                jitter = random.uniform(0, base_wait * 0.1)
                logger.warning(f"Network error on {endpoint}: {e}, retrying in {base_wait + jitter:.1f}s")
                await asyncio.sleep(base_wait + jitter)
        
        raise PinterestAPIError(f"Max retries ({self.config.max_retries}) exceeded for {endpoint}: {last_exception}")
    
    async def get_user_boards(self) -> list[Board]:
        """Fetch all boards for the authenticated user."""
        boards: list[Board] = []
        bookmark: Optional[str] = None
        
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            
            data = await self._request("GET", "/boards", params=params)
            
            items = data.get("items", [])
            if not isinstance(items, list):
                logger.warning("Unexpected response format: items is not a list")
                break
            
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    boards.append(Board(
                        id=str(item.get("id", "")),
                        name=str(item.get("name", "")),
                        description=str(item.get("description", "")),
                        pin_count=int(item.get("pin_count", 0)),
                        owner_id=str(item.get("owner", {}).get("id", "")),
                        url=item.get("url"),
                    ))
                except (KeyError, TypeError, ValueError) as e:
                    logger.warning(f"Skipping malformed board entry: {e}")
                    continue
            
            bookmark = data.get("bookmark")
            if not bookmark:
                break
        
        logger.info(f"Fetched {len(boards)} boards")
        return boards
    
    async def get_board_by_name(self, board_name: str) -> Optional[Board]:
        """Find a specific board by name (more efficient than fetching all)."""
        bookmark: Optional[str] = None
        target_name = board_name.lower().strip()
        
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            
            data = await self._request("GET", "/boards", params=params)
            
            items = data.get("items", [])
            if not isinstance(items, list):
                break
            
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("name", "")).lower().strip() == target_name:
                    try:
                        return Board(
                            id=str(item.get("id", "")),
                            name=str(item.get("name", "")),
                            description=str(item.get("description", "")),
                            pin_count=int(item.get("pin_count", 0)),
                            owner_id=str(item.get("owner", {}).get("id", "")),
                            url=item.get("url"),
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
            
            bookmark = data.get("bookmark")
            if not bookmark:
                break
        
        return None
    
    async def get_board_pins(self, board_id: str) -> list[Pin]:
        """Fetch all pins from a specific board."""
        pins: list[Pin] = []
        bookmark: Optional[str] = None
        
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            
            data = await self._request("GET", f"/boards/{board_id}/pins", params=params)
            
            items = data.get("items", [])
            if not isinstance(items, list):
                logger.warning("Unexpected response format: items is not a list")
                break
            
            for item in items:
                if not isinstance(item, dict):
                    continue
                pin = self._parse_pin(item, board_id)
                if pin:
                    pins.append(pin)
            
            bookmark = data.get("bookmark")
            if not bookmark:
                break
        
        logger.info(f"Fetched {len(pins)} pins from board {board_id}")
        return pins
    
    def _parse_pin(self, item: dict[str, Any], board_id: str) -> Optional[Pin]:
        """Parse pin data from API response."""
        try:
            media = item.get("media")
            images = item.get("images")
            
            if not isinstance(media, dict):
                media = {}
            if not isinstance(images, dict):
                images = {}
            
            media_url = self._get_best_quality_url(media, images)
            if not media_url or not self._is_valid_url(media_url):
                return None
            
            return Pin(
                id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                media_url=media_url,
                media_type=str(media.get("media_type", "image")),
                original_filename=self._extract_original_filename(item),
                board_id=board_id,
                created_at=str(item.get("created_at", "")),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Skipping malformed pin: {e}")
            return None
    
    def _is_valid_url(self, url: str) -> bool:
        """Check if URL is valid and not empty."""
        if not url or not isinstance(url, str):
            return False
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)
    
    def _get_best_quality_url(self, media: dict, images: dict) -> Optional[str]:
        """Get highest quality media URL available."""
        if media.get("media_type") == "video":
            video_url = self._extract_video_url(media)
            if video_url and self._is_valid_url(video_url):
                return video_url
        
        if images:
            for key in ["originals", "original", "1200x", "800x", "600x", "474x"]:
                sized = images.get(key)
                if isinstance(sized, dict):
                    url = sized.get("url")
                    if url and self._is_valid_url(url):
                        return url
                elif isinstance(sized, str) and self._is_valid_url(sized):
                    return sized
        
        return None
    
    def _extract_video_url(self, media: dict) -> Optional[str]:
        """Extract video download URL."""
        images = media.get("images")
        if not isinstance(images, dict):
            return None
        download = images.get("download")
        if not isinstance(download, dict):
            return None
        return download.get("url")
    
    def _extract_original_filename(self, item: dict) -> Optional[str]:
        """Extract original filename from pin data."""
        media = item.get("media")
        if not isinstance(media, dict):
            return None
        
        images = media.get("images")
        if not isinstance(images, dict):
            return None
        
        download = images.get("download")
        if not isinstance(download, dict):
            return None
        
        url = download.get("url", "")
        if not url or not isinstance(url, str):
            return None
        
        filename = url.split("/")[-1].split("?")[0]
        if filename and len(filename) > 2 and "." in filename:
            return filename
        return None
    
    @staticmethod
    def parse_board_url(url: str) -> tuple[str, str]:
        """Parse Pinterest board URL to extract username and board name."""
        url = url.strip()
        
        patterns = [
            r"(?:https?://)?(?:www\.)?pinterest\.[a-z]+/([^/]+)/([^/?#]+)/?",
            r"(?:https?://)?(?:www\.)?pinterest\.[a-z]+/([^/]+)/board/([^/?#]+)/?",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1), match.group(2)
        
        raise ValueError(f"Invalid Pinterest board URL: {url}")


class PinterestError(Exception):
    """Base exception for Pinterest API errors."""
    pass


class PinterestAuthError(PinterestError):
    """Authentication related errors."""
    pass


class PinterestAPIError(PinterestError):
    """API request errors."""
    pass


class PinterestNotFoundError(PinterestError):
    """Resource not found errors."""
    pass
