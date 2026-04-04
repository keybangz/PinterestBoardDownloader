# Pinterest Board Downloader

A CLI tool to download and archive Pinterest boards with highest quality media.

## Features

- **Multiple access methods**: API, web scraping, or browser automation
- Download all media from public or private Pinterest boards
- Configurable image quality: `736x` (default), original upload, or as-scraped — with automatic 404 fallback
- Create archives (ZIP, TAR, TAR.GZ, TAR.BZ2) for easy storage
- Async downloads with progress tracking and resume support
- Duplicate detection via content hashing
- Rate limiting and retry logic for stability
- File size limits and disk space checks
- SSL verification option
- Verbose logging mode for debugging
- Non-TTY output support
- **Reliable browser automation** with board-scoped extraction, `/pin/{numeric_id}/` filtering, DOM virtualization handling, and clean stall detection

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/pinterest-board-downloader.git
cd pinterest-board-downloader

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

Current runtime dependencies in `requirements.txt`:
- `aiohttp`
- `aiofiles`
- `click`
- `rich`
- `python-dotenv`
- `beautifulsoup4`
- `lxml`
- `playwright`

> Note: browser automation requires Playwright browser binaries:
>
> ```bash
> playwright install chromium
> ```

## Access Methods

This tool provides **three methods** to download Pinterest boards depending on your situation:

### Method 1: Web Scraping (Public-only fallback)
**Best for:** Public boards when you want a no-login workflow

```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name --method scrape
```

**Features:**
- No API credentials needed
- Works with public boards
- Uses multiple user-agents and rate limiting
- Extracts embedded JSON or falls back to HTML parsing

### Method 2: Browser Automation (Recommended for most users)
**Best for:** Most board downloads (public or private), especially when reliability matters

```bash
# Recommended first run: headed (interactive) login
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser

# Subsequent runs can be headless after cookies are saved
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser --headless
```

**Features:**
- Interactive login via browser (saves cookies for future use)
- Handles public, private, and shared boards
- Anti-detection launch settings and headless stealth scripts
- Correct viewport/screen settings for stable headless behavior
- Board-scoped extraction with `/pin/{numeric_id}/` gating to keep board pins and exclude related/sidebar/ads content
- Scroll loop exits cleanly after **3 consecutive no-growth checks**

> **Important headless workflow:** Run **headed once first** to complete login and save cookies. After that, `--headless` works reliably for repeat runs.

### Method 3: Pinterest API (Limited availability)
**Best for:** Business accounts with approved API access

```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name --method api
```

**Note:** Pinterest API access requires a **Business account** and app approval, which is often difficult to obtain.

## Setup

### Option A: Web Scraping / Browser Automation
**No API setup required.** Install dependencies and start downloading.

```bash
pip install -r requirements.txt
# For browser automation, install Playwright browsers:
playwright install chromium
```

For browser automation with `--headless`, use this workflow:
1. Run one download in headed mode (`--method browser`) and log in interactively
2. Let the run save `pinterest_cookies.json` in your output directory
3. Use `--headless` on future runs

### Option B: Pinterest API Access (Limited)

1. **Convert to Business account** (if you have a personal account):
    - Go to https://www.pinterest.com/settings/convert-to-business
    - Or create one at https://www.pinterest.com/business/create

2. **Apply for API access** (difficult process):
    - Go to https://developers.pinterest.com/apps/
    - Pinterest rarely approves new API applications

3. **If approved**, run:
```bash
python -m pinterest_downloader setup
```

4. Or manually create a `.env` file:
```
PINTEREST_ACCESS_TOKEN=your_access_token
```

## Usage

### Download a single board
```bash
# Browser automation (recommended for most users)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser

# Web scraping (public-only fallback)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method scrape

# API (only if you have approved credentials)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method api
```

### Download with options
```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name \
    --method browser \
    --output ./my_downloads \
    --archive zip
```

### Resume interrupted download
```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser --resume
```

### Download all your boards
```bash
# Download all boards (requires Pinterest API credentials)
python -m pinterest_downloader all-boards --archive tar.gz
```

> **Note:** `all-boards` only supports the API method. For browser-based downloading, use the `download` command with `--method browser` for each board individually.

### List boards
```bash
# List boards via API (requires credentials)
python -m pinterest_downloader list
```

### Headless browser mode
```bash
# First run headed once to save cookies
python -m pinterest_downloader download URL --method browser

# Then run headless
python -m pinterest_downloader download URL --method browser --headless
```

### Image quality

By default, downloads are upgraded to **736x** (the highest reliably-available Pinterest size for standard pins). Use `--quality` / `-q` to control this:

```bash
# Default: upgrades thumbnails to 736x automatically
python -m pinterest_downloader download URL --method browser

# Attempt full original upload resolution (falls back to 736x then as-scraped on 404)
python -m pinterest_downloader download URL --method browser --quality original

# Exact thumbnail URL as scraped (not recommended — may be 474x or smaller)
python -m pinterest_downloader download URL --method browser --quality default
```

| Mode | Candidate URLs tried (in order) | Use case |
|------|----------------------------------|----------|
| `large` (default) | `736x` → scraped URL | Best quality for nearly all pins |
| `original` | `originals` → `736x` → scraped URL | User-uploaded high-res art/photos |
| `default` | scraped URL only | Exact thumbnail, no upgrade |

> Pinterest CDN pin URLs follow the pattern `https://i.pinimg.com/{size}/xx/xx/xx/hash.jpg`. Only the size segment changes — `236x`, `474x`, `736x`, `originals`. The `736x` tier is available for virtually all pins; `/originals/` is only present for some user-uploaded content.

### Verbose mode (for debugging)
```bash
python -m pinterest_downloader -v download https://pinterest.com/username/board-name --method browser
```

### Interactive setup (API and/or browser credentials)
```bash
python -m pinterest_downloader setup
```

## Options

| Option | Description |
|--------|-------------|
| `-m, --method` | Access method: `scrape` (default), `browser`, `api` |
| `-o, --output` | Output directory for downloads (default: ./downloads) |
| `-a, --archive` | Archive format: zip, tar, tar.gz, tar.bz2, none |
| `-c, --config-file` | Path to .env config file |
| `-r, --resume` | Resume interrupted downloads |
| `--headless` | Run browser in headless mode (with `--method browser`) |
| `-q, --quality` | Image quality: `large` 736x (default), `original` full-res with fallback, `default` as-scraped |
| `-v, --verbose` | Enable verbose logging |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PINTEREST_APP_ID` | Pinterest App ID | - |
| `PINTEREST_APP_SECRET` | Pinterest App Secret | - |
| `PINTEREST_ACCESS_TOKEN` | OAuth Access Token | - |
| `PINTEREST_USERNAME` | Pinterest username/email for browser login | - |
| `PINTEREST_PASSWORD` | Pinterest password for browser login | - |
| `OUTPUT_DIR` | Default output directory | ./downloads |
| `MAX_CONCURRENT_DOWNLOADS` | Concurrent download limit | 5 |
| `REQUEST_TIMEOUT` | Request timeout in seconds | 30 |
| `CONNECT_TIMEOUT` | Connection timeout in seconds | 10 |
| `MAX_RETRIES` | Max retry attempts | 3 |
| `CHUNK_SIZE` | Download chunk size in bytes | 65536 |
| `VERIFY_DUPLICATES` | Check for duplicate content | true |
| `VERIFY_SSL` | Verify SSL certificates | true |
| `RATE_LIMIT_DELAY` | Delay between API/scrape calls | 0.1 |
| `MAX_HASH_CACHE_SIZE` | Max cached file hashes | 10000 |
| `MIN_FREE_DISK_MB` | Minimum free disk space (MB) | 100 |
| `MAX_FILE_SIZE_MB` | Maximum file size to download (MB) | 500 |
| `USER_AGENT` | HTTP User-Agent header | PinterestBoardDownloader/1.0.0 |

## Architecture

```
pinterest_downloader/
├── __init__.py              # Package initialization
├── __main__.py              # Entry point for python -m
├── config.py                # Configuration management
├── pinterest_client.py      # Pinterest API client with rate limiting
├── scraper.py               # Web scraping for public boards
├── browser_automation.py    # Playwright-based browser automation
├── downloader.py            # Async media downloader with resume support
├── archiver.py              # Archive creation (ZIP/TAR)
└── main.py                  # CLI entry point with Click
```

## Method Comparison

| Feature | Web Scraping | Browser Automation | Pinterest API |
|---------|:---:|:---:|:---:|
| **Public Boards** | ✓ Works | ✓ Works | ✓ Works |
| **Private Boards** | ✗ No | ✓ Works | ✓ Works |
| **Shared Boards** | ✗ No | ✓ Works | ✓ Works |
| **Credentials Required** | ✗ No | ✗ No (optional) | ✓ Yes (Business + approval) |
| **Recommended for most users** | ◐ Public-only | ✓ Yes | ✗ Limited access |
| **Setup Complexity** | Low | Low | High |
| **Rate Limiting / stability controls** | Built-in | Built-in | Built-in |
| **Performance** | Fast | Medium | Fast |

## Performance & Stability Features

- **Connection pooling**: Reuses HTTP connections for better performance
- **Concurrent downloads**: Configurable parallel downloads with semaphores
- **Rate limiting**: Prevents API throttling with token bucket algorithm
- **Resume support**: Skip already downloaded files
- **Content hashing**: Optional MD5 deduplication with bounded cache
- **Streaming writes**: Memory-efficient large file handling
- **Disk space checks**: Pre-download validation with configurable minimums
- **File size limits**: Skip files exceeding configured maximum
- **SSL verification**: Configurable SSL certificate verification
- **Graceful shutdown**: Signal handling for clean interruption
- **Error recovery**: Exponential backoff with capped retries
- **Bounded memory**: LRU eviction for hash cache to prevent memory growth
- **Board-accurate browser extraction**: Board feed scoping + numeric pin ID filtering for near-complete board pin capture while excluding non-board content
- **Quality-aware downloads**: Configurable CDN size-tier selection with automatic 404 fallback chain
- **Clean stall detection**: Scroll loop exits after 3 consecutive no-growth checks

## Error Handling

**API Mode:**
- HTTP 429 (Rate Limited): Automatic retry with Retry-After header
- HTTP 5xx (Server Error): Exponential backoff up to 30 seconds
- HTTP 401/403 (Auth Error): Clear error message with guidance

**Scraping & Browser Mode:**
- 404 Not Found: Clear error reporting
- Quality fallback: On `--quality original`, 404 on `/originals/` automatically retries at `736x` then scraped URL
- Login required: Clean message with method suggestion
- Rate limiting: Built-in delays and retries
- Headless login constraint: Explicit guidance to run headed once to save cookies

**All Modes:**
- Disk Full: Early detection and graceful failure
- Permission Denied: Clear error reporting
- Network Errors: Automatic retry with backoff
- Cancellation: Graceful shutdown (Ctrl+C) saves state

## Troubleshooting

### "Private board, need login" Error
```bash
# Switch to browser automation
python -m pinterest_downloader download URL --method browser
```

### "403 Forbidden" Error
```bash
# Browser automation is typically more reliable
python -m pinterest_downloader download URL --method browser
```

### Browser doesn't open
```bash
# Install Playwright browsers
playwright install chromium
```

### Headless mode can't log in
Headless mode cannot complete manual login prompts.

Use this sequence:
1. Run once in headed mode and log in:
   ```bash
   python -m pinterest_downloader download URL --method browser
   ```
2. Re-run with headless:
   ```bash
   python -m pinterest_downloader download URL --method browser --headless
   ```

### Pin counts differ from Pinterest UI count
Current browser extraction is board-scoped and filters to valid `/pin/{numeric_id}/` links. This is intentional:
- Includes board pins with near-complete accuracy
- Excludes related pins, sidebar suggestions, and ad/non-board content
- Stops scrolling cleanly after 3 consecutive no-growth checks to avoid endless loops

If a board still appears short, retry with browser automation (headed once, then headless if desired):
```bash
python -m pinterest_downloader download URL --method browser
```

### API access denied
Unfortunately, Pinterest rarely approves new API applications.
Use browser automation or web scraping instead.

## License

MIT License
