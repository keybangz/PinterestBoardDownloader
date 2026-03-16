"""Patch for networkidle timeout issues."""

from typing import Optional
import asyncio
from pathlib import Path
import sys

# Read the current file
file_path = Path(__file__).parent / "pinterest_downloader/browser_automation.py"
content = file_path.read_text()

# Find and replace problem patterns
replacements = [
    # Change to safer waits
    ("wait_until='networkidle'", "wait_until='domcontentloaded'"),
    ("timeout=60000", "timeout=30000"),
    ("timeout=20000", "timeout=30000"),
]

modified = False
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f"Replaced '{old}' with '{new}'")
        modified = True

if modified:
    # Backup original
    backup = file_path.with_suffix(file_path.suffix + ".backup")
    file_path.rename(backup)
    
    # Write modified
    file_path.write_text(content)
    print(f"Patched {file_path.name}")
else:
    print("No changes needed")

# Output what changed
if content.count("networkidle") > 0:
    print(f"WARNING: {content.count('networkidle')} networkidle calls still remain")