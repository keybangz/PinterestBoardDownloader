# Pinterest Board Downloader

A CLI tool to download and archive Pinterest boards with highest quality media.

## Features

- **Multiple access methods**: API, web scraping, or browser automation
- Download all media from public or private Pinterest boards
- Highest quality image/video downloads with original filenames
- Create archives (ZIP, TAR, TAR.GZ, TAR.BZ2) for easy storage
- Async downloads with progress tracking and resume support
- Duplicate detection via content hashing
- Rate limiting and retry logic for stability
- File size limits and disk space checks
- SSL verification option
- Verbose logging mode for debugging
- Non-TTY output support
- **Improved browser automation** with DOM virtualization handling (typically captures 95-98% of pins)

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

## Access Methods

This tool provides **three methods** to download Pinterest boards depending on your situation:

### Method 1: Web Scraping (Recommended for Public Boards)
**Best for:** Public boards, no credentials required

```bash
# Automatically uses scraping for public boards
python -m pinterest_downloader download https://pinterest.com/username/board-name --method scrape
```

**Features:**
- No API credentials needed
- Works with public boards
- Uses multiple user-agents and rate limiting
- Extracts embedded JSON or falls back to HTML parsing

### Method 2: Browser Automation (Recommended for Private Boards)
**Best for:** Private boards, shared boards, boards requiring login

```bash
# Opens browser for interactive login
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser

# Headless mode (requires prior login)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser --headless
```

**Features:**
- Interactive login via browser (saves cookies for future use)
- Handles boards requiring login
- Scrolls to load all pins dynamically
- No API credentials needed

### Method 3: Pinterest API (Limited availability)
**Best for:** Business accounts with approved API access

```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name --method api
```

**Note:** Pinterest API access requires a **Business account** and app approval, which is often difficult to obtain.

## Setup

### Option A: Web Scraping / Browser Automation
**No setup required!** Just install dependencies and start downloading.

```bash
pip install -r requirements.txt
# For browser automation, install Playwright browsers:
playwright install chromium
```

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
# Web scraping (recommended for public boards)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method scrape

# Browser automation (recommended for private boards)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method browser

# API (only if you have approved credentials)
python -m pinterest_downloader download https://pinterest.com/username/board-name --method api
```

### Download with options
```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name \
    --method scrape \
    --output ./my_downloads \
    --archive zip
```

### Resume interrupted download
```bash
python -m pinterest_downloader download https://pinterest.com/username/board-name --method scrape --resume
```

### Download all your boards
```bash
# Using browser automation (requires login)
python -m pinterest_downloader all-boards --method browser --archive tar.gz

# Using API (if you have credentials)
python -m pinterest_downloader all-boards --method api --archive tar.gz
```

### List boards
```bash
# List boards via browser (interactive)
python -m pinterest_downloader list --method browser

# List boards via API (requires credentials)
python -m pinterest_downloader list --method api
```

### Headless browser mode
```bash
# Login once with interactive mode, then use headless
python -m pinterest_downloader download URL --method browser --headless
```

### Verbose mode (for debugging)
```bash
python -m pinterest_downloader -v download https://pinterest.com/username/board-name --method scrape
```

### Interactive setup (API only)
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
| `--headless` | Run browser in headless mode |
| `-v, --verbose` | Enable verbose logging |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PINTEREST_APP_ID` | Pinterest App ID | - |
| `PINTEREST_APP_SECRET` | Pinterest App Secret | - |
| `PINTEREST_ACCESS_TOKEN` | OAuth Access Token | - |
| `OUTPUT_DIR` | Default output directory | ./downloads |
| `MAX_CONCURRENT_DOWNLOADS` | Concurrent download limit | 5 |
| `REQUEST_TIMEOUT` | Request timeout in seconds | 30 |
| `MAX_RETRIES` | Max retry attempts | 3 |
| `CHUNK_SIZE` | Download chunk size in bytes | 65536 |
| `VERIFY_DUPLICATES` | Check for duplicate content | true |
| `VERIFY_SSL` | Verify SSL certificates | true |
| `RATE_LIMIT_DELAY` | Delay between API calls | 0.1 |
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

| Feature | Web Scraping Browser Automation | Pinterest API |
|---------|-----------|-------------|
| **Public Boards** | ✓ Works | ✓ Works |
| **Private Boards** | ✓ Works | ✓ Works |
| **Shared Boards** | ✓ Works | ✓ Works |
| **Credentials Required** | ✗ No | ✓ Yes (Business + approval) |
| **Setup Complexity** | Low | High |
| **Compilation Risk** | Low | High |
| **Rate Limiting** | Built-in | Built-in |
| **Performance** | Medium | Fast |

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

## Error Handling

**API Mode:**
- HTTP 429 (Rate Limited): Automatic retry with Retry-After header
- HTTP 5xx (Server Error): Exponential backoff up to 30 seconds
- HTTP 401/403 (Auth Error): Clear error message with guidance

**Scraping & Browser Mode:**
- 404 Not Found: Clear error reporting
- Login required: Clean message with method suggestion
- Rate limiting: Built-in delays and retries

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
# Try browser automation instead of scraping
python -m pinterest_downloader download URL --method browser
```

### Browser doesn't open
```bash
# Install Playwright browsers
playwright install chromium
```

### API access denied
Unfortunately, Pinterest rarely approves new API applications.
Use browser automation or web scraping instead.

### Missing pins with browser automation
The browser automation method has been improved to handle Pinterest's DOM virtualization:
- Extracts pins incrementally during scrolling before they're removed from DOM
- Uses URL-based deduplication for accurate pin counting
- Typically captures 95-98% of expected pins (43/44 in tests)

## License

MIT License
