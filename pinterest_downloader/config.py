"""Configuration management for Pinterest Board Downloader."""

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_env_int(key: str, default: int) -> int:
    """Safely parse integer environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Invalid integer value for {key}, using default: {default}")
        return default


def _get_env_float(key: str, default: float) -> float:
    """Safely parse float environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning(f"Invalid float value for {key}, using default: {default}")
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    """Safely parse boolean environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def _get_env_path(key: str, default: str) -> Path:
    """Parse path environment variable."""
    value = os.getenv(key, default)
    return Path(value).expanduser().resolve()


@dataclass
class Config:
    """Configuration settings for the downloader."""
    
    # API credentials
    app_id: Optional[str] = field(default_factory=lambda: os.getenv("PINTEREST_APP_ID"))
    app_secret: Optional[str] = field(default_factory=lambda: os.getenv("PINTEREST_APP_SECRET"))
    access_token: Optional[str] = field(default_factory=lambda: os.getenv("PINTEREST_ACCESS_TOKEN"))
    refresh_token: Optional[str] = field(default_factory=lambda: os.getenv("PINTEREST_REFRESH_TOKEN"))
    
    # Browser login credentials (for automated login)
    pinterest_username: Optional[str] = field(default_factory=lambda: os.getenv("PINTEREST_USERNAME"))
    pinterest_password: Optional[str] = field(default_factory=lambda: os.getenv("PINTEREST_PASSWORD"))
    
    output_dir: Path = field(default_factory=lambda: _get_env_path("OUTPUT_DIR", "./downloads"))
    max_concurrent_downloads: int = field(default_factory=lambda: min(max(1, _get_env_int("MAX_CONCURRENT_DOWNLOADS", 5)), 20))
    request_timeout: int = field(default_factory=lambda: min(max(5, _get_env_int("REQUEST_TIMEOUT", 30)), 300))
    connect_timeout: int = field(default_factory=lambda: min(max(5, _get_env_int("CONNECT_TIMEOUT", 10)), 60))
    max_retries: int = field(default_factory=lambda: min(max(0, _get_env_int("MAX_RETRIES", 3)), 10))
    chunk_size: int = field(default_factory=lambda: min(max(4096, _get_env_int("CHUNK_SIZE", 65536)), 1048576))
    verify_duplicates: bool = field(default_factory=lambda: _get_env_bool("VERIFY_DUPLICATES", True))
    rate_limit_delay: float = field(default_factory=lambda: min(max(0.01, _get_env_float("RATE_LIMIT_DELAY", 0.1)), 10.0))
    max_hash_cache_size: int = field(default_factory=lambda: min(max(100, _get_env_int("MAX_HASH_CACHE_SIZE", 10000)), 100000))
    min_free_disk_mb: int = field(default_factory=lambda: min(max(10, _get_env_int("MIN_FREE_DISK_MB", 100)), 10000))
    max_file_size_mb: int = field(default_factory=lambda: min(max(1, _get_env_int("MAX_FILE_SIZE_MB", 500)), 5000))
    verify_ssl: bool = field(default_factory=lambda: _get_env_bool("VERIFY_SSL", True))
    user_agent: str = field(default_factory=lambda: os.getenv("USER_AGENT", "PinterestBoardDownloader/1.0.0")[:256])
    
    def __post_init__(self) -> None:
        if not isinstance(self.output_dir, Path):
            object.__setattr__(self, 'output_dir', Path(self.output_dir).expanduser().resolve())
    
    @property
    def has_api_credentials(self) -> bool:
        """Check if API credentials are configured."""
        return bool(self.access_token and self.access_token.strip())
    
    @property
    def has_browser_credentials(self) -> bool:
        """Check if browser login credentials are configured."""
        return bool(self.pinterest_username and self.pinterest_password 
                    and self.pinterest_username.strip() and self.pinterest_password.strip())
    
    def ensure_output_dir(self) -> Path:
        """Create output directory if it doesn't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir
    
    def check_disk_space(self, required_mb: int = 100) -> tuple[bool, Optional[int]]:
        """Check if there's enough disk space. Returns (has_space, free_mb)."""
        try:
            path = self.output_dir if self.output_dir.exists() else self.output_dir.parent
            if not path.exists():
                path = Path(".")
            total, used, free = shutil.disk_usage(path)
            free_mb = free // (1024 * 1024)
            return free_mb >= required_mb, free_mb
        except (OSError, AttributeError) as e:
            logger.warning(f"Could not check disk space: {e}")
            return True, None
    
    def is_output_writable(self) -> bool:
        """Check if output directory is writable."""
        try:
            path = self.output_dir
            if path.exists():
                test_file = path / ".write_test"
                test_file.touch()
                test_file.unlink()
                return True
            parent = path.parent
            if parent.exists():
                test_file = parent / ".write_test"
                test_file.touch()
                test_file.unlink()
                return True
        except (OSError, PermissionError):
            pass
        return False
