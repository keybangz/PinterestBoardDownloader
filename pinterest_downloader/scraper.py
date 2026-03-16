"""Web scraper for public Pinterest boards without API access."""

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .config import Config
from .pinterest_client import Pin

logger = logging.getLogger(__name__)


@dataclass
class ScrapedPin:
    """Pin data extracted from web scraping."""
    id: str
    title: str
    description: str
    media_url: str
    media_type: str
    original_filename: Optional[str]
    
    def to_pin(self, board_id: str = "") -> Pin:
        """Convert to Pin dataclass."""
        return Pin(
            id=self.id,
            title=self.title,
            description=self.description,
            media_url=self.media_url,
            media_type=self.media_type,
            original_filename=self.original_filename,
            board_id=board_id,
            created_at=""
        )


class PinterestScraper:
    """Scrape public Pinterest boards without API access."""
    
    BASE_URL = "https://www.pinterest.com"
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    
    def __init__(self, config: Config) -> None:
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._closed: bool = False
        self._request_count: int = 0
    
    async def __aenter__(self) -> "PinterestScraper":
        if self._closed:
            raise RuntimeError("Scraper has already been closed")
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create aiohttp session with browser-like headers."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.request_timeout,
                connect=self.config.connect_timeout,
                sock_read=self.config.request_timeout
            )
            connector = aiohttp.TCPConnector(
                limit=5,
                limit_per_host=2,
                ttl_dns_cache=300,
                force_close=False,
                ssl=self.config.verify_ssl
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=self._get_browser_headers()
            )
        return self._session
    
    def _get_browser_headers(self) -> dict[str, str]:
        """Get headers that mimic a real browser."""
        ua = random.choice(self.USER_AGENTS)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
    
    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._closed = True
    
    async def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page with rate limiting and retry logic."""
        session = await self._ensure_session()
        
        for attempt in range(self.config.max_retries):
            try:
                await asyncio.sleep(self.config.rate_limit_delay + random.uniform(0, 0.5))
                
                async with session.get(url) as response:
                    if response.status == 200:
                        self._request_count += 1
                        return await response.text()
                    elif response.status == 429:
                        wait = random.uniform(30, 60)
                        logger.warning(f"Rate limited, waiting {wait:.0f}s")
                        await asyncio.sleep(wait)
                        continue
                    elif response.status == 404:
                        logger.error(f"Page not found: {url}")
                        return None
                    elif response.status == 403:
                        logger.error(f"Access forbidden: {url}")
                        return None
                    else:
                        logger.warning(f"HTTP {response.status} for {url}")
                        if attempt < self.config.max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return None
                        
            except aiohttp.ClientError as e:
                logger.warning(f"Request failed: {e}")
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None
        
        return None
    
    def _extract_json_data(self, html: str) -> Optional[dict[str, Any]]:
        """Extract Pinterest's embedded JSON data from HTML."""
        soup = BeautifulSoup(html, 'lxml')
        
        for script_id in ['__PWS_DATA__', '__NEXT_DATA__', 'initial-state']:
            script = soup.find('script', id=script_id)
            if script and script.string:
                try:
                    return json.loads(script.string)
                except json.JSONDecodeError:
                    continue
        
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'pin' in script.string.lower() and 'images' in script.string.lower():
                try:
                    match = re.search(r'\{.*"pins".*\}', script.string, re.DOTALL)
                    if match:
                        return json.loads(match.group())
                except (json.JSONDecodeError, AttributeError):
                    continue
        
        return None
    
    def _parse_pin_from_data(self, pin_data: dict[str, Any]) -> Optional[ScrapedPin]:
        """Parse pin data from JSON structure."""
        try:
            pin_id = pin_data.get('id') or pin_data.get('pin_id', '')
            if not pin_id:
                return None
            
            title = pin_data.get('title', '') or pin_data.get('grid_title', '')
            description = pin_data.get('description', '') or pin_data.get('grid_description', '')
            
            images = pin_data.get('images', {})
            media = pin_data.get('media', {})
            
            media_url = None
            media_type = 'image'
            
            if media and media.get('type') == 'video':
                video_images = media.get('images', {})
                download = video_images.get('download', {})
                media_url = download.get('url')
                media_type = 'video'
            
            if not media_url and images:
                for size in ['orig', 'originals', '1200x', '800x', '600x', '474x']:
                    sized = images.get(size, {})
                    if isinstance(sized, dict):
                        media_url = sized.get('url')
                        if media_url:
                            break
                    elif isinstance(sized, str):
                        media_url = sized
                        break
            
            if not media_url:
                url_field = pin_data.get('url', '') or pin_data.get('image_url', '')
                if url_field and self._is_valid_url(url_field):
                    media_url = url_field
            
            if not media_url or not self._is_valid_url(media_url):
                return None
            
            original_filename = None
            if media_url:
                filename = media_url.split('/')[-1].split('?')[0]
                if filename and '.' in filename:
                    original_filename = filename
            
            return ScrapedPin(
                id=str(pin_id),
                title=str(title or ''),
                description=str(description or ''),
                media_url=media_url,
                media_type=media_type,
                original_filename=original_filename
            )
            
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse pin: {e}")
            return None
    
    def _is_valid_url(self, url: str) -> bool:
        """Check if URL is valid."""
        if not url or not isinstance(url, str):
            return False
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)
    
    async def get_board_pins_from_url(self, board_url: str) -> list[Pin]:
        """Scrape all pins from a public board URL."""
        pins: list[Pin] = []
        
        board_url = board_url.strip()
        if not board_url.startswith('http'):
            board_url = f"https://www.pinterest.com/{board_url}"
        
        board_url = board_url.rstrip('/')
        
        html = await self._fetch_page(board_url)
        if not html:
            logger.error(f"Failed to fetch board page: {board_url}")
            return pins
        
        data = self._extract_json_data(html)
        if not data:
            logger.warning("Could not extract JSON data, trying HTML parsing")
            pins_data = self._parse_pins_from_html(html)
        else:
            pins_data = self._extract_pins_from_json(data)
        
        for pin_data in pins_data:
            scraped = self._parse_pin_from_data(pin_data)
            if scraped:
                pins.append(scraped.to_pin())
        
        logger.info(f"Scraped {len(pins)} pins from {board_url}")
        return pins
    
    def _extract_pins_from_json(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract pin data from Pinterest's JSON structure with deduplication."""
        pins: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        
        def find_pins_recursive(obj: Any, depth: int = 0) -> None:
            if depth > 15 or not isinstance(obj, (dict, list)):
                return
            
            if isinstance(obj, dict):
                # Check if this is a pin-like object
                if 'id' in obj and ('images' in obj or 'image_url' in obj or 'media' in obj):
                    pin_id = str(obj.get('id', ''))
                    if pin_id and pin_id not in seen_ids:
                        seen_ids.add(pin_id)
                        pins.append(obj)
                
                for value in obj.values():
                    find_pins_recursive(value, depth + 1)
                    
            elif isinstance(obj, list):
                for item in obj:
                    find_pins_recursive(item, depth + 1)
        
        find_pins_recursive(data)
        return pins
    
    def _parse_pins_from_html(self, html: str) -> list[dict[str, Any]]:
        """Fallback: parse pins directly from HTML with deduplication."""
        pins: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        
        soup = BeautifulSoup(html, 'lxml')
        
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if 'pinimg.com' in src and '/236x/' not in src:
                pin_id = ''
                alt = img.get('alt', '')
                
                # Try to get ID from parent link
                parent = img.find_parent('a')
                if parent:
                    href = parent.get('href', '')
                    match = re.search(r'/pin/(\w+)', href)
                    if match:
                        pin_id = match.group(1)
                
                # Fallback: get ID from image URL
                if not pin_id:
                    # Try multiple patterns for alphanumeric IDs
                    match = re.search(r'/\d+x\w*/(\w+)/', src) or \
                            re.search(r'/originals/(\w+)/', src) or \
                            re.search(r'/(\w+)\.', src)
                    if match:
                        pin_id = match.group(1)
                
                # Add if valid and unique
                if pin_id and pin_id not in seen_ids:
                    seen_ids.add(pin_id)
                    pins.append({
                        'id': pin_id,
                        'title': alt,
                        'images': {'orig': {'url': src.replace('/236x/', '/originals/').replace('/474x/', '/originals/')}},
                    })
        
        return pins
