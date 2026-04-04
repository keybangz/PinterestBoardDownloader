"""Media downloader module for downloading Pinterest pins."""

import asyncio
import hashlib
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp

from .config import Config
from .pinterest_client import Pin

logger = logging.getLogger(__name__)


_PINIMG_SIZE_RE = re.compile(r"(https://i\.pinimg\.com/)([^/]+)(/\w{2}/\w{2}/\w{2}/.+)")


def _resolve_quality_candidates(url: str, quality: str) -> list[str]:
    """
    Build an ordered list of CDN URL candidates for the requested quality.
    Only rewrites Pinterest CDN (i.pinimg.com) size-segment URLs.
    Falls back through sizes on 404. Always ends with the original URL.
    """
    if quality == "default":
        return [url]

    m = _PINIMG_SIZE_RE.match(url)
    if not m:
        return [url]  # Not a pinimg URL — no rewrite

    prefix, current_size, suffix = m.group(1), m.group(2), m.group(3)

    def make(size: str) -> str:
        return f"{prefix}{size}{suffix}"

    already_original = current_size in ("originals", "orig")
    already_large = current_size in ("736x", "600x", "800x", "1200x")

    if quality == "large":
        if already_large or already_original:
            return [url]
        # Upgrade small thumbnail to 736x, fall back to original URL
        return list(dict.fromkeys([make("736x"), url]))

    if quality == "original":
        if already_original:
            return [url]
        candidates = [make("originals")]
        if not already_large:
            candidates.append(make("736x"))
        candidates.append(url)
        return list(dict.fromkeys(candidates))  # deduplicate while preserving order

    return [url]


@dataclass
class DownloadResult:
    """Result of a pin download operation."""

    pin: Pin
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None
    skipped: bool = False
    bytes_downloaded: int = 0


class DownloadError(Exception):
    """Download related errors."""

    pass


class DiskFullError(DownloadError):
    """Disk is full."""

    pass


class PermissionDeniedError(DownloadError):
    """Permission denied."""

    pass


class BoundedSet:
    """Set with maximum size that evicts oldest entries."""

    def __init__(self, max_size: int = 10000) -> None:
        self._data: OrderedDict = OrderedDict()
        self._max_size = max_size

    def add(self, key: str) -> None:
        """Add key, evicting oldest if at capacity."""
        if key in self._data:
            self._data.move_to_end(key)
        else:
            self._data[key] = None
            if len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)


class MediaDownloader:
    """Async media downloader with progress tracking and resume support."""

    def __init__(
        self, config: Config, output_dir: Path, quality: str = "default"
    ) -> None:
        self.config = config
        self.output_dir = output_dir
        self.quality = quality
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._downloaded_hashes = BoundedSet(config.max_hash_cache_size)
        self._file_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._closed: bool = False

    async def __aenter__(self) -> "MediaDownloader":
        if self._closed:
            raise RuntimeError("Downloader has already been closed")
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_downloads)
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create aiohttp session with optimized settings."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.request_timeout * 5,
                connect=self.config.connect_timeout,
                sock_read=self.config.request_timeout * 2,
            )
            connector = aiohttp.TCPConnector(
                limit=self.config.max_concurrent_downloads * 2,
                limit_per_host=self.config.max_concurrent_downloads,
                ttl_dns_cache=300,
                force_close=False,
                ssl=self.config.verify_ssl,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                raise_for_status=False,
                headers={"User-Agent": self.config.user_agent},
            )
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session and cleanup."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._file_locks.clear()
        self._closed = True

    async def _get_file_lock(self, filename: str) -> asyncio.Lock:
        """Get or create a lock for a specific file."""
        async with self._global_lock:
            if filename not in self._file_locks:
                self._file_locks[filename] = asyncio.Lock()
            return self._file_locks[filename]

    async def download_pins(
        self, pins: list[Pin], progress=None, existing_files: Optional[set[str]] = None
    ) -> list[DownloadResult]:
        """Download all pins concurrently with optional resume support."""
        if not pins:
            return []

        existing = existing_files or set()
        to_download: list[Pin] = []
        skipped_results: list[DownloadResult] = []

        for pin in pins:
            expected_path = self._get_expected_path(pin.filename)
            if expected_path.name in existing and expected_path.exists():
                skipped_results.append(
                    DownloadResult(
                        pin=pin, success=True, file_path=expected_path, skipped=True
                    )
                )
            else:
                to_download.append(pin)

        task_id = None
        if progress:
            task_id = progress.add_task("[cyan]Downloading...", total=len(to_download))

        async def download_with_progress(pin: Pin) -> DownloadResult:
            result = await self._download_pin(pin)
            if progress and task_id is not None:
                progress.update(task_id, advance=1)
            return result

        results = list(skipped_results)
        if to_download:
            tasks = [download_with_progress(pin) for pin in to_download]
            try:
                raw_results = await asyncio.gather(*tasks, return_exceptions=True)

                for r, p in zip(raw_results, to_download):
                    if isinstance(r, DownloadResult):
                        results.append(r)
                    elif isinstance(r, asyncio.CancelledError):
                        results.append(DownloadResult(p, False, error="Cancelled"))
                        raise r
                    elif isinstance(r, Exception):
                        results.append(DownloadResult(p, False, error=str(r)))
                    else:
                        results.append(DownloadResult(p, False, error="Unknown error"))
            finally:
                self._file_locks.clear()

        return results

    def _get_expected_path(self, filename: str) -> Path:
        """Get expected file path for a filename."""
        return self.output_dir / filename

    async def _download_pin(self, pin: Pin) -> DownloadResult:
        """Download a single pin with retry logic."""
        if self._semaphore is None:
            raise RuntimeError("Downloader not initialized - use async context manager")

        async with self._semaphore:
            last_error: Optional[str] = None

            for attempt in range(self.config.max_retries):
                try:
                    return await self._do_download(pin)
                except DiskFullError as e:
                    logger.error(f"Disk full, cannot download pin {pin.id}")
                    return DownloadResult(pin, False, error=str(e))
                except PermissionDeniedError as e:
                    logger.error(f"Permission denied for pin {pin.id}")
                    return DownloadResult(pin, False, error=str(e))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_error = str(e)
                    if attempt == self.config.max_retries - 1:
                        logger.error(f"Failed to download pin {pin.id}: {e}")
                        return DownloadResult(pin, False, error=last_error)
                    wait = min(2**attempt, 30)
                    logger.warning(f"Retry {attempt + 1} for pin {pin.id} in {wait}s")
                    await asyncio.sleep(wait)

            return DownloadResult(
                pin, False, error=last_error or "Max retries exceeded"
            )

    async def _do_download(self, pin: Pin) -> DownloadResult:
        """Perform actual download of a pin."""
        session = await self._ensure_session()

        base_filename = pin.filename
        file_lock = await self._get_file_lock(base_filename)

        async with file_lock:
            file_path = self._get_unique_path(base_filename)
            temp_path = file_path.with_suffix(file_path.suffix + ".tmp")

            if temp_path.exists():
                try:
                    temp_path.unlink()
                except PermissionError:
                    raise PermissionDeniedError(f"Cannot write to {temp_path}")

            bytes_downloaded = 0

            try:
                candidates = _resolve_quality_candidates(pin.media_url, self.quality)

                for attempt_url in candidates:
                    if attempt_url != pin.media_url:
                        logger.debug(f"Quality upgrade attempt: {attempt_url}")
                    async with session.get(attempt_url) as response:
                        if response.status == 404:
                            logger.debug(
                                f"404 on {attempt_url}, trying next quality candidate"
                            )
                            continue
                        elif response.status == 200:
                            pass
                        elif response.status == 403:
                            return DownloadResult(
                                pin, False, error="Access denied (403)"
                            )
                        else:
                            raise DownloadError(
                                f"HTTP {response.status}: Failed to download"
                            )

                        bytes_downloaded = 0
                        content_length = response.content_length
                        if content_length:
                            max_size_bytes = self.config.max_file_size_mb * 1024 * 1024
                            if content_length > max_size_bytes:
                                return DownloadResult(
                                    pin,
                                    False,
                                    error=f"File too large ({content_length // (1024 * 1024)}MB > {self.config.max_file_size_mb}MB)",
                                )
                            has_space, free_mb = self.config.check_disk_space(
                                max(
                                    self.config.min_free_disk_mb,
                                    content_length // (1024 * 1024) + 10,
                                )
                            )
                            if not has_space:
                                raise DiskFullError(
                                    f"Insufficient disk space (free: {free_mb}MB)"
                                )

                        content_hash = (
                            hashlib.md5() if self.config.verify_duplicates else None
                        )
                        chunk_size = self.config.chunk_size

                        try:
                            async with aiofiles.open(temp_path, "wb") as f:
                                async for chunk in response.content.iter_chunked(
                                    chunk_size
                                ):
                                    await f.write(chunk)
                                    bytes_downloaded += len(chunk)
                                    if content_hash:
                                        content_hash.update(chunk)
                        except OSError as e:
                            if e.errno == 28:
                                raise DiskFullError("Disk full during download")
                            elif e.errno == 13:
                                raise PermissionDeniedError(
                                    f"Permission denied: {temp_path}"
                                )
                            raise

                        if content_hash:
                            file_hash = content_hash.hexdigest()
                            if file_hash in self._downloaded_hashes:
                                temp_path.unlink()
                                return DownloadResult(
                                    pin,
                                    True,
                                    file_path,
                                    "Duplicate (already downloaded)",
                                    True,
                                )
                            self._downloaded_hashes.add(file_hash)

                        try:
                            temp_path.rename(file_path)
                        except OSError as e:
                            if e.errno == 13:
                                raise PermissionDeniedError(
                                    f"Cannot rename to {file_path}"
                                )
                            raise

                        logger.debug(
                            f"Downloaded: {file_path.name} ({bytes_downloaded} bytes)"
                        )

                        return DownloadResult(
                            pin, True, file_path, bytes_downloaded=bytes_downloaded
                        )

                return DownloadResult(
                    pin,
                    False,
                    error="Media not found (404 on all quality candidates)",
                )

            except asyncio.CancelledError:
                self._safe_unlink(temp_path)
                raise
            except Exception:
                self._safe_unlink(temp_path)
                raise

    def _safe_unlink(self, path: Path) -> None:
        """Safely delete a file, ignoring errors."""
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _get_unique_path(self, filename: str) -> Path:
        """Generate unique file path if file already exists."""
        file_path = self.output_dir / filename

        if not file_path.exists():
            return file_path

        stem = file_path.stem
        suffix = file_path.suffix
        counter = 1
        max_attempts = 10000

        while file_path.exists() and counter < max_attempts:
            new_name = f"{stem}_{counter:04d}{suffix}"
            file_path = self.output_dir / new_name
            counter += 1

        if file_path.exists():
            import uuid

            file_path = self.output_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

        return file_path

    def get_existing_files(self) -> set[str]:
        """Get set of already downloaded filenames."""
        if not self.output_dir.exists():
            return set()

        try:
            return {
                f.name
                for f in self.output_dir.iterdir()
                if f.is_file() and not f.name.endswith(".tmp")
            }
        except PermissionError:
            logger.warning(f"Cannot read directory: {self.output_dir}")
            return set()
