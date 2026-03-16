"""Archive creation module for downloaded Pinterest media."""

import io
import logging
import re
import shutil
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional, cast

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


class Archiver:
    """Creates archives from downloaded media."""
    
    MEDIA_EXTENSIONS = frozenset([
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".mp4", ".mov", ".avi", ".mkv", ".webm"
    ])
    
    def __init__(self, output_dir: Path, compression_level: int = 6) -> None:
        self.output_dir = output_dir
        self.compression_level = max(0, min(9, compression_level))
    
    def create_archive(
        self,
        archive_format: str = "zip",
        board_name: Optional[str] = None,
        include_metadata: bool = True,
        cleanup: bool = False,
    ) -> Optional[Path]:
        """Create an archive of all downloaded media."""
        media_files = self._get_media_files()
        
        if not media_files:
            logger.warning(f"No media files found in {self.output_dir}")
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_board_name = self._sanitize_name(board_name or "boards")
        archive_name = f"pinterest_{safe_board_name}_{timestamp}"
        
        try:
            if archive_format == "zip":
                archive_path = self._create_zip(archive_name, media_files, include_metadata)
            elif archive_format in ("tar", "tar.gz", "tar.bz2"):
                archive_path = self._create_tar(archive_name, archive_format, media_files, include_metadata)
            else:
                raise ValueError(f"Unsupported archive format: {archive_format}")
            
            console.print(f"[green]Archive created:[/green] {archive_path}")
            
            if cleanup:
                self.cleanup()
            
            return archive_path
            
        except PermissionError as e:
            logger.error(f"Permission denied creating archive: {e}")
            raise
        except OSError as e:
            logger.error(f"Failed to create archive: {e}")
            raise
    
    def _get_unique_archive_path(self, base_path: Path) -> Path:
        """Get unique archive path if file already exists."""
        if not base_path.exists():
            return base_path
        
        stem = base_path.stem
        suffix = base_path.suffix
        counter = 1
        
        while base_path.exists():
            base_path = base_path.parent / f"{stem}_{counter}{suffix}"
            counter += 1
        
        return base_path
    
    def _create_zip(self, archive_name: str, media_files: list[Path], include_metadata: bool) -> Path:
        """Create a ZIP archive with compression."""
        archive_path = self._get_unique_archive_path(
            self.output_dir.parent / f"{archive_name}.zip"
        )
        
        total_size = 0
        added_count = 0
        
        try:
            with zipfile.ZipFile(
                archive_path, "w",
                zipfile.ZIP_DEFLATED,
                compresslevel=self.compression_level
            ) as zf:
                for file_path in media_files:
                    try:
                        file_size = self._safe_get_size(file_path)
                        if file_size is None:
                            continue
                        
                        arcname = file_path.relative_to(self.output_dir)
                        zf.write(file_path, arcname)
                        total_size += file_size
                        added_count += 1
                    except PermissionError as e:
                        logger.warning(f"Permission denied reading {file_path}: {e}")
                    except OSError as e:
                        logger.warning(f"Failed to add {file_path}: {e}")
                
                if include_metadata:
                    self._add_metadata_to_zip(zf, media_files, added_count, total_size)
            
            if added_count == 0:
                archive_path.unlink()
                raise ValueError("No files could be added to archive")
            
            logger.info(f"Created ZIP archive with {added_count} files ({self._format_size(total_size)})")
            return archive_path
            
        except Exception:
            if archive_path.exists():
                try:
                    archive_path.unlink()
                except Exception:
                    pass
            raise
    
    def _create_tar(
        self,
        archive_name: str,
        archive_format: str,
        media_files: list[Path],
        include_metadata: bool
    ) -> Path:
        """Create a TAR archive."""
        extension = "." + archive_format if archive_format != "tar" else ".tar"
        archive_path = self._get_unique_archive_path(
            self.output_dir.parent / f"{archive_name}{extension}"
        )
        
        mode_map: dict[str, Literal["w", "w:gz", "w:bz2"]] = {
            "tar": "w",
            "tar.gz": "w:gz",
            "tar.bz2": "w:bz2"
        }
        mode = mode_map.get(archive_format, "w")
        
        total_size = 0
        added_count = 0
        
        try:
            with tarfile.open(archive_path, cast(Literal["w", "w:gz", "w:bz2"], mode)) as tf:
                for file_path in media_files:
                    try:
                        file_size = self._safe_get_size(file_path)
                        if file_size is None:
                            continue
                        
                        arcname = file_path.relative_to(self.output_dir)
                        tf.add(file_path, arcname)
                        total_size += file_size
                        added_count += 1
                    except PermissionError as e:
                        logger.warning(f"Permission denied reading {file_path}: {e}")
                    except OSError as e:
                        logger.warning(f"Failed to add {file_path}: {e}")
                
                if include_metadata:
                    self._add_metadata_to_tar(tf, media_files, added_count, total_size)
            
            if added_count == 0:
                archive_path.unlink()
                raise ValueError("No files could be added to archive")
            
            logger.info(f"Created TAR archive with {added_count} files ({self._format_size(total_size)})")
            return archive_path
            
        except Exception:
            if archive_path.exists():
                try:
                    archive_path.unlink()
                except Exception:
                    pass
            raise
    
    def _safe_get_size(self, file_path: Path) -> Optional[int]:
        """Safely get file size, returning None on error."""
        try:
            return file_path.stat().st_size
        except (OSError, PermissionError):
            return None
    
    def _get_media_files(self) -> list[Path]:
        """Get all media files in output directory."""
        if not self.output_dir.exists():
            return []
        
        files: list[Path] = []
        
        try:
            for file_path in self.output_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in self.MEDIA_EXTENSIONS:
                    files.append(file_path)
        except PermissionError as e:
            logger.warning(f"Permission error scanning directory: {e}")
        
        return sorted(files)
    
    def _add_metadata_to_zip(
        self,
        zf: zipfile.ZipFile,
        media_files: list[Path],
        added_count: int,
        total_size: int
    ) -> None:
        """Add metadata file to ZIP archive."""
        metadata = self._generate_metadata(media_files, added_count, total_size)
        zf.writestr("archive_info.txt", metadata)
    
    def _add_metadata_to_tar(
        self,
        tf: tarfile.TarFile,
        media_files: list[Path],
        added_count: int,
        total_size: int
    ) -> None:
        """Add metadata file to TAR archive."""
        metadata = self._generate_metadata(media_files, added_count, total_size)
        metadata_bytes = metadata.encode("utf-8")
        
        info = tarfile.TarInfo(name="archive_info.txt")
        info.size = len(metadata_bytes)
        info.mtime = int(datetime.now().timestamp())
        
        tf.addfile(info, io.BytesIO(metadata_bytes))
    
    def _generate_metadata(
        self,
        media_files: list[Path],
        added_count: int,
        total_size: int
    ) -> str:
        """Generate archive metadata."""
        file_list_lines = [f"  - {f.relative_to(self.output_dir)}" for f in media_files[:100]]
        if len(media_files) > 100:
            file_list_lines.append(f"  ... and {len(media_files) - 100} more files")
        
        return f"""Pinterest Board Archive
{'=' * 40}
Created: {datetime.now().isoformat()}
Total Files: {added_count}
Total Size: {self._format_size(total_size)}
Source: Pinterest Board Downloader v1.0.0

File List:
{chr(10).join(file_list_lines)}
"""
    
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in human-readable format."""
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"
    
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize name for use in filename."""
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('_')[:50]
    
    def cleanup(self) -> bool:
        """Remove downloaded files after archiving."""
        if not self.output_dir.exists():
            return True
        
        try:
            shutil.rmtree(self.output_dir)
            console.print(f"[yellow]Cleaned up:[/yellow] {self.output_dir}")
            return True
        except PermissionError as e:
            logger.error(f"Permission denied cleaning up {self.output_dir}: {e}")
            return False
        except OSError as e:
            logger.error(f"Failed to cleanup {self.output_dir}: {e}")
            return False
