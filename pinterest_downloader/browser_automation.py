"""Browser automation for Pinterest using Playwright - Auto-Login Version."""

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from .config import Config
from .pinterest_client import Pin

console = Console()
logger = logging.getLogger(__name__)

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    Browser = None
    Page = None
    BrowserContext = None


@dataclass
class LoginCredentials:
    """Pinterest login credentials."""

    username: str
    password: str


class BrowserAuthError(Exception):
    """Browser authentication errors."""

    pass


class PinterestBrowser:
    """Browser automation for Pinterest access."""

    BASE_URL = "https://www.pinterest.com"
    COOKIES_FILE = "pinterest_cookies.json"

    def __init__(
        self,
        config: Config,
        headless: bool = False,
        shutdown_event: Optional[asyncio.Event] = None,
    ) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. Install with:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )
        self.config = config
        self.headless = headless
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._closed: bool = False
        self._credentials: Optional[LoginCredentials] = None
        self._shutdown_event: Optional[asyncio.Event] = shutdown_event

        # Auto-load credentials from config
        if config.pinterest_username and config.pinterest_password:
            self._credentials = LoginCredentials(
                username=config.pinterest_username, password=config.pinterest_password
            )

    async def __aenter__(self) -> "PinterestBrowser":
        if self._closed:
            raise RuntimeError("Browser has already been closed")
        await self._start_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _start_browser(self) -> None:
        """Start the browser with saved cookies if available."""
        playwright = await async_playwright().start()

        # Build launch args - use start-maximized for headed mode
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--no-sandbox",
            "--start-maximized",
        ]

        self._browser = await playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
        )

        cookies_path = Path(self.config.output_dir) / self.COOKIES_FILE
        cookies_path.parent.mkdir(parents=True, exist_ok=True)

        # Don't set viewport - let browser use maximized size automatically
        self._context = await self._browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            storage_state=None,  # Don't auto-load, we'll decide
        )

        # Try loading cookies first
        if cookies_path.exists():
            try:
                cookies = json.loads(cookies_path.read_text())
                session_cookies = [
                    c
                    for c in cookies
                    if c.get("name") in ("_pinterest_sess", "csrftoken")
                ]
                if not session_cookies:
                    logger.warning(
                        "Cookie file exists but contains no Pinterest session cookies "
                        "(_pinterest_sess / csrftoken). Session may be expired or anonymous."
                    )
                await self._context.add_cookies(cookies)
                logger.info(
                    f"Loaded {len(cookies)} saved cookies ({len(session_cookies)} auth cookies)"
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Could not load cookies: {e}")

        self._page = await self._context.new_page()

    async def close(self) -> None:
        """Close the browser."""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        self._page = None
        self._context = None
        self._browser = None
        self._closed = True

    async def ensure_logged_in(self) -> bool:
        """Automated or manual login with credentials.

        Priority order:
        1. Check existing cookies
        2. Use configured credentials
        3. Manual browser login
        """
        if not self._page:
            raise RuntimeError("Browser not started")

        console.print("\n[bold]Verifying Pinterest login...[/bold]")

        # Check 1: Quick page check without navigation
        if await self._quick_login_check():
            console.print("[green]✓ Already logged in using saved session[/green]")
            return True

        # Check 2: Try with saved cookies via homepage
        try:
            await self._page.goto(
                f"{self.BASE_URL}/", wait_until="domcontentloaded", timeout=15000
            )
            await asyncio.sleep(2)
            if await self._has_login_indicators():
                # Optionally verify we're logged in as the expected account
                if self.config.pinterest_username:
                    expected = self.config.pinterest_username.lower().strip()
                    try:
                        profile_link = await self._page.query_selector(
                            f'a[href="/{expected}/"], a[href*="/{expected}"]'
                        )
                        if profile_link is None:
                            logger.warning(
                                f"Session verified but profile link for '{expected}' not found — "
                                "cookies may belong to a different account"
                            )
                            console.print(
                                f"[yellow]⚠ Session verified but could not confirm account is '{expected}'[/yellow]"
                            )
                    except Exception:
                        pass
                console.print(
                    "[green]✓ Valid session verified (authenticated elements found)[/green]"
                )
                await self._save_or_update_cookies()
                return True
        except Exception as e:
            logger.debug(f"Cookie login check failed: {e}")

        # Check 3: Use configured credentials for auto-login
        if self._credentials:
            console.print("[cyan]Using saved credentials to log in...[/cyan]")
            success = await self._perform_auto_login()
            if success:
                console.print(
                    "[green]✓ Successfully logged in with credentials[/green]"
                )
                await self._save_or_update_cookies()
                return True
            else:
                console.print("[yellow]Auto-login failed, will try manual[/yellow]")

        # Check 4: Manual interactive login
        return await self._perform_manual_login()

    async def _quick_login_check(self) -> bool:
        """Return True only when a positively-authenticated DOM element is found.

        Uses an explicit whitelist of selectors that ONLY appear when a user
        is signed in. Avoids the permissive 'not on login page = logged in'
        heuristic that produced false positives on Pinterest's public homepage.
        """
        if not self._page:
            return False

        url = self._page.url
        # Fast-path: definitely not logged in
        if "login" in url.lower() or url.endswith("/login"):
            return False
        # Fast-path: blank/unloaded page - no auth check possible
        if not url or url == "about:blank":
            return False

        # Selectors that ONLY appear in an authenticated session
        AUTHED_SELECTORS = [
            '[data-test-id="header-profile"]',  # Profile button in top nav
            'div[data-test-id="header-user-menu"]',  # User dropdown menu
            '[aria-label="Switch account"]',  # Multi-account badge (signed-in)
            'a[href="/settings/"]',  # Settings link (signed-in only)
            'button[aria-label*="Your profile"]',
            'button[aria-label*="profile picture"]',
        ]

        # Presence of this element confirms NOT logged in (guest upsell CTA)
        NEGATIVE_SELECTORS = [
            'a[href="/business/create"]',
        ]

        try:
            for selector in NEGATIVE_SELECTORS:
                try:
                    el = await self._page.query_selector(selector)
                    if el:
                        logger.debug(f"Not-logged-in indicator found: {selector}")
                        return False
                except Exception:
                    pass

            for selector in AUTHED_SELECTORS:
                try:
                    el = await self._page.query_selector(selector)
                    if el:
                        logger.debug(f"Auth indicator found: {selector}")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # No positive authenticated indicator found — treat as not logged in
        return False

    async def _has_login_indicators(self) -> bool:
        """Check if current page shows logged-in state (without navigation)."""
        return await self._quick_login_check()

    async def _perform_auto_login(self) -> bool:
        """Use credentials to automatically log in - handles 2-button login flow."""
        if not self._credentials or not self._page:
            return False

        # Step 1: Load login page with LOCK - prevent any other navigation
        console.print("  [dim]→ Loading Pinterest login page...[/dim]")
        try:
            await self._page.goto(
                f"{self.BASE_URL}/login", wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(2)  # Wait for dynamic content
        except Exception as e:
            console.print(f"[yellow]Login page slow: {e}[/yellow]")
            return False

        # Check if already on credentials form, or if first button needed
        # STEP 1: Handle potential initial "Log In" button on landing page
        initial_button_selectors = [
            'button:has-text("Log in")',
            'button:has-text("Login")',
            'button[aria-label="Log in"]',
            'button[type="submit"]',
        ]

        username_input = None
        for selector in [
            'input[aria-label*="user"]',
            'input[aria-label*="email"]',
            'input[name="username"]',
            "input#email",
        ]:
            username_input = await self._page.query_selector(selector)
            if username_input:
                break

        # If we DON'T see username field but DO see login buttons on main lander,
        # we need to click first button to open credentials form
        if not username_input:
            console.print(
                "  [dim]→ First login click (opening credentials form)…[/dim]"
            )
            for selector in initial_button_selectors:
                element = await self._page.query_selector(selector)
                if element:
                    await element.click()
                    await asyncio.sleep(2)  # Wait for form to slide in
                    break

        # STEP 2: Wait for and verify username field appears
        username_selectors = [
            'input[aria-label="Email, username, or phone"]',
            'input[aria-label="Email or phone number"]',
            "input#email",
            "input#username",
            'input[name="username"]',
            'input[name="email"]',
            'input[type="email"]',
            'input[aria-label*="email"]',
            'input[aria-label*="username"]',
            'input[placeholder*="email"]',
            'input[placeholder*="user"]',
        ]

        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            "input#password",
            'input[placeholder*="password"]',
        ]

        # DEBUG output
        console.print("  [dim]→ Looking for credential fields...[/dim]")

        # Enter username with retry logic
        username_entered = False
        for retry in range(3):
            for selector in username_selectors:
                element = await self._page.query_selector(selector)
                if element:
                    try:
                        await element.fill(self._credentials.username)
                        username_entered = True
                        console.print(f"  [dim]→ Username entered[/dim]")
                        break
                    except Exception:
                        continue
            if username_entered:
                break
            await asyncio.sleep(1)

        if not username_entered:
            console.print("[red]✗ Could not find username field[/red]")
            return False

        await asyncio.sleep(0.5)

        # Enter password with retry logic
        password_entered = False
        for retry in range(3):
            for selector in password_selectors:
                element = await self._page.query_selector(selector)
                if element:
                    try:
                        await element.fill(self._credentials.password)
                        password_entered = True
                        console.print(f"  [dim]→ Password entered[/dim]")
                        break
                    except Exception:
                        continue
            if password_entered:
                break
            await asyncio.sleep(1)

        if not password_entered:
            console.print("[red]✗ Could not find password field[/red]")
            return False

        await asyncio.sleep(0.5)

        # STEP 3: SECOND LOGIN BUTTON CLICK (actual authorization)
        login_button_selectors = [
            'button[type="submit"]',
            'button:has-text("Log in")',
            'button:has-text("Login")',
            'button[aria-label="Log in"]',
            'button:has-text("Continue")',
        ]

        button_clicked = False
        for selector in login_button_selectors:
            element = await self._page.query_selector(selector)
            if element:
                try:
                    # Wait a moment for form validation
                    await asyncio.sleep(0.3)
                    await element.click()
                    button_clicked = True
                    console.print("  [dim]→ Login credentials submitted[/dim]")
                    break
                except Exception as e:
                    logger.debug(f"Click failed for {selector}: {e}")
                    continue

        if not button_clicked:
            # Fallback: Keyboard Enter
            try:
                await self._page.keyboard.press("Enter")
                button_clicked = True
                console.print("  [dim]→ Submitted via Enter key[/dim]")
            except Exception:
                pass

        if not button_clicked:
            console.print("[red]✗ Could not click login button[/red]")
            return False

        # STEP 4: WAIT FOR LOGIN COMPLETION - no other navigation during this
        console.print("  [dim]→ Waiting for login to complete...[/dim]")

        for i in range(30):  # Check for 60 seconds
            await asyncio.sleep(2)

            current_url = self._page.url

            # Success if NOT on any login page
            if "login" not in current_url.lower():
                # Additional verification
                console.print("  [dim]→ Redirect detected, verifying...[/dim]")
                await asyncio.sleep(1)  # Brief settle

                if await self._has_login_indicators():
                    console.print("[green]✓ Login successful![/green]")
                    return True

            # Still on login? Show progress
            if i % 5 == 4:
                console.print(f"[dim]Still on login... ({(i + 1) * 2}s)[/dim]")

        console.print("[red]✗ Login timeout - manual intervention needed[/red]")
        return False

    async def _perform_manual_login(self) -> bool:
        """Manual login with timed verification."""
        console.print(
            "\n[yellow]⚠ Credentials not configured or auto-login failed[/yellow]"
        )
        console.print("[cyan]Manual login required[/cyan]")
        console.print("[cyan]  1. Browser will open Pinterest login page[/cyan]")
        console.print("[cyan]  2. Log in to Pinterest in the browser[/cyan]")
        console.print(
            "[cyan]  3. Once logged in, wait here (auto-detects completion)[/cyan]"
        )

        try:
            await self._page.goto(
                f"{self.BASE_URL}/login", wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(2)
        except Exception as e:
            console.print(f"[red]Failed to load login page: {e}[/red]")
            console.print("[yellow]Using current page instead...[/yellow]")

        console.print("\n[bold]Waiting for manual login...[/bold]")

        for i in range(150):
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                return False

            await asyncio.sleep(2)

            if await self._has_login_indicators():
                console.print("[green]✓ Login detected[/green]")
                await self._save_or_update_cookies()
                return True

            if (i + 1) % 10 == 0:
                seconds = (i + 1) * 2
                console.print(f"[dim]Still waiting... ({seconds}s)[/dim]")

        console.print("[red]Login timeout[/red]")
        return False

    async def _save_or_update_cookies(self) -> bool:
        """Save current cookies to file."""
        if not self._context:
            return False

        try:
            cookies_path = Path(self.config.output_dir) / self.COOKIES_FILE
            cookies = await self._context.cookies()
            cookies_path.write_text(json.dumps(cookies, indent=2))
            logger.info(f"Cookies saved to {cookies_path}")
            return True
        except Exception as e:
            logger.warning(f"Could not save cookies: {e}")
            return False

    async def get_board_pins_from_url(self, board_url: str) -> list[Pin]:
        """Get all pins from a board URL using browser."""
        if not self._page:
            raise RuntimeError("Browser not started")

        pins: list[Pin] = []

        # Normalize URL
        board_url = board_url.strip()
        if not board_url.startswith("http"):
            board_url = f"{self.BASE_URL}/{board_url}"
        board_url = board_url.rstrip("/")

        # DHCP-IMMEDIATE-CHECK: Verify login before ANY navigation
        # This is the definitive fix for 'board loads during login' bug
        current_url = self._page.url

        # If on login page right now, return with error - nothing to do
        if "login" in current_url.lower():
            console.print("[red]✗ Board access attempted while on login page![/red]")
            console.print("[yellow]  Wait for login to complete first.[/yellow]")
            return pins

        # Check login indicators explicitly
        if not await self._has_login_indicators():
            console.print("[red]✗ Login verification failed before board access![/red]")
            # Try to recover - but don't leave user hanging
            console.print("[yellow]  Attempting to verify login state...[/yellow]")
            if not await self.ensure_logged_in():
                return pins

        # PROCEED ONLY if we're definitely logged in
        console.print(f"\n[cyan]Navigating to board: {board_url}[/cyan]")

        # Use safe navigation
        try:
            await self._page.goto(
                board_url, wait_until="domcontentloaded", timeout=30000
            )
            await asyncio.sleep(3)
        except Exception as e:
            console.print(f"[yellow]Timed out: {e}[/yellow]")
            console.print("[dim]Continuing with current state...[/dim]")

        # Handle redirect (session expiry etc.)
        if "login" in self._page.url.lower():
            console.print(
                "[yellow]Session expired during navigation. Re-logging in...[/yellow]"
            )
            if not await self.ensure_logged_in():
                return pins

            # Retry once
            try:
                await self._page.goto(
                    board_url, wait_until="domcontentloaded", timeout=30000
                )
                await asyncio.sleep(3)
            except:
                pass

            if "login" in self._page.url.lower():
                return pins

        # Scroll to load ALL pins first and get URLs discovered during scrolling
        discovered_urls, scroll_extracted_pins = await self._scroll_to_load_all()
        console.print(
            f"[dim]Discovered {len(discovered_urls)} unique image URLs during scrolling, extracted {len(scroll_extracted_pins)} pin data entries[/dim]"
        )

        # Use pins extracted DURING scrolling (captures virtualized content)
        console.print("[cyan]Processing pins extracted during scrolling...[/cyan]")
        pins_data = scroll_extracted_pins

        # Also run post-scroll extraction for comparison/debug
        await asyncio.sleep(2)
        post_scroll_pins_data = await self._extract_pins_from_page()

        # Debug comparison
        scroll_urls = {
            pin.get("image_url", "")
            for pin in scroll_extracted_pins
            if pin.get("image_url")
        }
        post_scroll_urls = {
            pin.get("image_url", "")
            for pin in post_scroll_pins_data
            if pin.get("image_url")
        }

        missing_in_post = scroll_urls - post_scroll_urls
        if missing_in_post:
            console.print(
                f"[yellow]Warning: {len(missing_in_post)} pins captured during scrolling missing in post-scroll extraction[/yellow]"
            )
            console.print(
                f"[dim]This confirms DOM virtualization - scroll extraction is more complete[/dim]"
            )

        # Use scroll-extracted pins (more complete due to virtualization)
        for pin_data in pins_data:
            pin = self._create_pin_from_data(pin_data)
            if pin:
                pins.append(pin)

        if not pins:
            console.print(
                "[yellow]No pins extracted. Possible login or access issue.[/yellow]"
            )

        console.print(
            f"[green]Extracted {len(pins)} pins from scrolling (scrolling saw {len(discovered_urls)} unique URLs)[/green]"
        )
        return pins

    async def _scroll_to_load_all(self) -> tuple[set[str], list[dict]]:
        """Aggressively scroll to load ALL pins on the board.

        Returns:
            Tuple of (unique pin image URLs, list of extracted pin data).
        """
        if not self._page:
            return set(), []

        console.print("[dim]Starting scroll sequence to catch all pins...[/dim]")

        # EXHAUSTIVE SCROLL PARAMETERS: Force full content loading
        max_same = 3  # Need 3 no-growth signals before exiting
        scroll_attempts = 0
        max_attempts = 600  # Ensure full page coverage (was 40)

        last_pin_count = 0  # Current scroll's pin counter
        pin_growth_stalled = 0  # Consecutive checks with no NEW pins

        # PERSISTENT PIN CACHE: Track ALL pins discovered across all scrolls
        all_discovered_pins: set = set()  # Store unique pin URLs
        all_extracted_pins: list = []  # Store full pin data
        max_pins_ever = 0  # Maximum pins seen at any point

        # BALANCED DELAY: Allow lazy loading while being efficient
        base_delay = 1.0  # 1 second between scrolls

        console.print(
            f"[dim]Config: max {max_attempts} scrolls, {max_same} no-growth checks needed[/dim]"
        )

        async def smart_scroll(scroll_num: int, total_height: float) -> None:
            """Simple progressive scroll from top to bottom using page height."""
            viewport = await self._page.evaluate("window.innerHeight")
            scrollable = total_height - viewport

            if scrollable <= 0:
                await self._page.evaluate("window.scrollTo(0, 0)")
                return

            # Divide scrollable height into 6 steps
            steps = 6
            step_size = scrollable / steps

            # Calculate target position based on scroll_num
            target = scroll_num * step_size

            # Clamp to max scrollable
            if target > scrollable:
                target = scrollable

            await self._page.evaluate(f"window.scrollTo(0, {target})")

        viewport = await self._page.evaluate("window.innerHeight")
        max_attempts = int(
            (await self._page.evaluate("document.body.scrollHeight") - viewport) / 200
        )
        max_attempts = max(max_attempts, 20)  # floor at 20 scrolls

        console.print(
            f"[dim]Calculated max scroll count based on page height: {max_attempts}[/dim]"
        )

        while scroll_attempts < max_attempts:
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                console.print("[yellow]Scroll interrupted by shutdown signal[/yellow]")
                break
            scroll_attempts += 1

            await smart_scroll(
                scroll_attempts, await self._page.evaluate("document.body.scrollHeight")
            )
            await asyncio.sleep(
                base_delay + random.uniform(0.5, 1.5)
            )  # Randomize delay

            try:
                # Extract pin data during scrolling to catch virtualized content
                extracted_pins = await self._page.evaluate("""
                    () => {
                        const pins = [];
                        const seenUrls = new Set();
                        
                        const images = document.querySelectorAll('img[src*="pinimg"], img[src*="cdn.pinimg"], img[src*="i.pinimg"]');
                        
                        images.forEach(img => {
                            if (!img.src || img.src.includes('data:image')) return;
                            const src = img.src.toLowerCase();
                            if (src.includes('profile') || src.includes('avatar') ||
                                src.includes('logo') || src.includes('icon') ||
                                src.includes('button') || src.includes('pinterest')) return;
                                
                            const url = img.src;
                            if (seenUrls.has(url)) return;
                            seenUrls.add(url);
                            
                            // Minimal pin data extraction (similar to full extraction)
                            const pinData = {};
                            pinData.image_url = url;
                            
                            // Title from alt or URL
                            if (img.alt && img.alt.length > 3 && !img.alt.includes('Pin card') && img.alt !== 'Image') {
                                pinData.title = img.alt.substring(0, 200);
                            } else {
                                const urlMatch = url.match(/\\/([a-zA-Z0-9]+)(?:\\/|\\.)/);
                                pinData.title = urlMatch ? 'Pin_' + urlMatch[1] : 'Pin';
                            }
                            
                            // Simplified ID extraction
                            let pinId = null;
                            const urlIdMatch = url.match(/\\/\\d+x\\w*\\/([a-zA-Z0-9]+)/) || 
                                            url.match(/\\/originals\\/([a-zA-Z0-9]+)/) || 
                                            url.match(/\\/([a-zA-Z0-9]+)(?:\\/|\\.)/);
                            if (urlIdMatch) pinId = urlIdMatch[1];
                            
                            if (!pinId) {
                                // Fallback hash
                                let hash = 0;
                                for (let i = 0; i < url.length; i++) {
                                    hash = ((hash << 5) - hash) + url.charCodeAt(i);
                                    hash |= 0;
                                }
                                pinId = 'url_' + Math.abs(hash).toString(36);
                            }
                            
                            pinData.id = pinId;
                            pins.push(pinData);
                        });
                        
                        return { pins: pins, count: pins.length };
                    }
                """)

                pin_count = extracted_pins["count"]
                current_pins = extracted_pins["pins"]

                # Extract URLs and deduplicate
                current_urls = set()
                for pin in current_pins:
                    image_url = pin.get("image_url")
                    if image_url:
                        current_urls.add(image_url)

                        # Add new pins to extracted list
                        if image_url not in all_discovered_pins:
                            all_extracted_pins.append(pin)

                # Update persistent cache with any NEW pins discovered
                new_pins = current_urls - all_discovered_pins
                all_discovered_pins.update(new_pins)
                max_pins_ever = max(max_pins_ever, len(all_discovered_pins))

                # Track pin growth using MAX pins ever seen
                if len(all_discovered_pins) > max_pins_ever:
                    max_pins_ever = len(all_discovered_pins)

                if len(all_discovered_pins) > last_pin_count:
                    console.print(
                        f"[dim]  Scroll {scroll_attempts}: GROWING {last_pin_count} → {len(all_discovered_pins)} (max: {max_pins_ever})[/dim]"
                    )
                    pin_growth_stalled = 0
                else:
                    pin_growth_stalled += 1
                    console.print(
                        f"[dim]  Scroll {scroll_attempts}: STALLED at {len(all_discovered_pins)} ({pin_growth_stalled}/3)[/dim]"
                    )

                last_pin_count = len(all_discovered_pins)

                # Exit at hard ceiling
                if scroll_attempts >= max_attempts:
                    console.print(
                        f"\n[yellow]✓ Reached max scroll limit ({max_attempts} scrolls, {len(all_discovered_pins)} pins)[/yellow]"
                    )
                    return all_discovered_pins, all_extracted_pins
                # Early exit when pin count has been stable for max_same consecutive checks
                MIN_SCROLLS = 10
                if pin_growth_stalled >= max_same and scroll_attempts >= MIN_SCROLLS:
                    console.print(
                        f"\n[yellow]✓ Exited after {scroll_attempts} scrolls (stable at {len(all_discovered_pins)} pins, extracted {len(all_extracted_pins)} pin data entries)[/yellow]"
                    )
                    return all_discovered_pins, all_extracted_pins

            except Exception as e:
                logger.debug(f"Scroll {scroll_attempts} extract error: {e}")

            # Visual progress for long scrolls
            if scroll_attempts > 0 and scroll_attempts % 10 == 0:
                console.print(
                    f"\n[dim]→ Scroll progress: {scroll_attempts}/{max_attempts}, pins seen: {last_pin_count}[/dim]"
                )

        return all_discovered_pins, all_extracted_pins

    async def _extract_pins_from_page(self) -> list[dict[str, Any]]:
        """Extract pin data from loaded page with deduplication."""
        if not self._page:
            return []

        pins = await self._page.evaluate(r"""
            () => {
                const pins = [];
                const seenUrls = new Set();  // Deduplicate by URL instead of ID
                let skippedByFilter = 0;
                let skippedByDedup = 0;
                let processedCount = 0;

                // EXACTLY match scroll detection: get all pin-related images
                const allImages = document.querySelectorAll('img[src*="pinimg"], img[src*="cdn.pinimg"], img[src*="i.pinimg"]');

                console.log(`Found ${allImages.length} total images for extraction`);

                allImages.forEach(img => {
                    if (!img.src || img.src.includes('data:image')) {
                        skippedByFilter++;
                        return;
                    }

                    // EXACTLY match scroll detection filters
                    const src = img.src.toLowerCase();
                    if (src.includes('profile') || src.includes('avatar') || 
                        src.includes('logo') || src.includes('icon') ||
                        src.includes('button') || src.includes('pinterest')) {
                        skippedByFilter++;
                        return;
                    }

                    const pinData = {};

                    // Use original src WITHOUT transformation during extraction
                    // Keep URL exactly as found - transformation happens later
                    let url = img.src;
                    pinData.image_url = url;

                    // Get title from alt or derive from URL
                    if (img.alt && img.alt.length > 3 && !img.alt.includes('Pin card') && img.alt !== 'Image') {
                        pinData.title = img.alt.substring(0, 200);
                    } else {
                        // Generate title from URL
                        const urlMatch = url.match(/\/([a-zA-Z0-9]+)(?:\/|\.)/);
                        if (urlMatch) {
                            pinData.title = 'Pin_' + urlMatch[1];
                        } else {
                            pinData.title = 'Pin';
                        }
                    }

                    // Get ID - simplified approach
                    let pinId = null;

                    // 1. First try: Extract from URL pattern (most reliable)
                    // Pattern: /{size}/{id}/ or /{size}/{id}. or /originals/{id}/
                    const urlIdMatch = url.match(/\/\d+x\w*\/([a-zA-Z0-9]+)/) || 
                                      url.match(/\/originals\/([a-zA-Z0-9]+)/) || 
                                      url.match(/\/([a-zA-Z0-9]+)(?:\/|\.)/);
                    if (urlIdMatch) pinId = urlIdMatch[1];

                    // 2. Try parent container data attributes
                    if (!pinId) {
                        const container = img.closest('[data-test-pin-id]') || img.closest('[data-pin-id]') || 
                                        img.closest('[data-id]') || img.closest('[data-test-id="pin"]');
                        if (container) {
                            pinId = container.getAttribute('data-test-pin-id') || 
                                   container.getAttribute('data-pin-id') || 
                                   container.getAttribute('data-id');
                        }
                    }

                    // 3. Try link href pattern
                    if (!pinId) {
                        const link = img.closest('a');
                        if (link && link.href) {
                            const match = link.href.match(/\/pin\/([a-zA-Z0-9]+)/);
                            if (match) pinId = match[1];
                        }
                    }

                    // 4. Generate from URL if needed - simpler approach
                    if (!pinId) {
                        // Use hash of URL as fallback ID
                        let hash = 0;
                        for (let i = 0; i < url.length; i++) {
                            hash = ((hash << 5) - hash) + url.charCodeAt(i);
                            hash |= 0;
                        }
                        pinId = 'url_' + Math.abs(hash).toString(36);
                    }

                    // Deduplicate by IMAGE URL (not ID) to match scroll counting
                    if (seenUrls.has(url)) {
                        skippedByDedup++;
                        return;
                    }
                    seenUrls.add(url);

                    pinData.id = pinId;
                    pins.push(pinData);
                    processedCount++;
                });

                console.log(`Extraction stats: Total=${allImages.length}, SkippedFilter=${skippedByFilter}, SkippedDedup=${skippedByDedup}, Extracted=${pins.length}`);
                return pins;
            }
        """)

        return pins or []

    def _create_pin_from_data(self, data: dict[str, Any]) -> Optional[Pin]:
        """Create Pin object from extracted data."""
        try:
            pin_id = str(data.get("id", ""))
            if not pin_id:
                return None

            image_url = data.get("image_url", "")
            if not image_url:
                return None

            # Transform URL to get higher quality image if possible
            # Similar to what scraper.py does
            transformed_url = image_url
            if "/originals/" not in transformed_url:
                # Replace size patterns like /236x/, /474x/, /736x/, etc. with /originals/
                transformed_url = re.sub(r"/\d+x\w*/", "/originals/", transformed_url)
                # Also handle patterns like _736x.jpg -> _original.jpg
                transformed_url = re.sub(r"_\d+x\.", "_original.", transformed_url)

            title = str(data.get("title", "") or f"Pin_{pin_id}")
            description = str(data.get("description", title))

            return Pin(
                id=pin_id,
                title=title[:200],
                description=description[:500],
                media_url=transformed_url,
                media_type="image",
                original_filename=None,
                board_id="",
                created_at="",
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Failed to create pin: {e}")
            return None

    async def get_user_boards(self) -> list[tuple[str, str]]:
        """Get list of user's boards (name, URL)."""
        if not self._page:
            return []

        username = getattr(self.config, "pinterest_username", None)
        if username:
            profile_url = f"{self.BASE_URL}/{username}/boards/"
        else:
            profile_url = f"{self.BASE_URL}/"
        await self._page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        try:
            boards = await self._page.evaluate("""
                () => {
                    const boards = [];
                    const seen = new Set();
                    const boardLinks = document.querySelectorAll('a[href*="/boards/"], a[href*="/created/"]');

                    boardLinks.forEach(link => {
                        const name = link.textContent?.trim();
                        const href = link.href;
                        if (name && href && !seen.has(href)) {
                            seen.add(href);
                            boards.push([name, href]);
                        }
                    });

                    return boards.slice(0, 50);
                }
            """)

            return boards or []
        except Exception as e:
            logger.error(f"Failed to get user boards: {e}")
            return []
