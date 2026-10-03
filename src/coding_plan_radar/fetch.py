"""Polite HTTP fetching with robots.txt awareness and local snapshots."""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import Vendor

USER_AGENT = "coding-plan-radar/0.1 (+https://github.com/OpenLlamas/coding-plan-radar)"


@dataclass
class FetchResult:
    vendor_id: str
    url: str
    status: int | None = None
    html: str = ""
    fetched_at: str = ""
    error: str = ""
    from_cache: bool = False
    render_used: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.html) and not self.error


def _utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Fetcher:
    """Fetch pricing pages, honour robots.txt, keep every response on disk.

    Snapshots make later runs reproducible offline (``--offline``) and are what
    change detection compares across weeks.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        timeout: float = 25.0,
        delay: float = 1.5,
        max_retries: int = 2,
        render: bool = False,
        offline: bool = False,
        ignore_robots: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.timeout = timeout
        self.delay = delay
        self.max_retries = max_retries
        self.render = render
        self.offline = offline
        self.ignore_robots = ignore_robots
        self._client: httpx.Client | None = None
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._requests_made = 0

    # ------------------------------------------------------------------ public

    def fetch(self, vendor: Vendor) -> FetchResult:
        url = vendor.pricing_url
        result = FetchResult(vendor_id=vendor.id, url=url, fetched_at=_utc_stamp())
        if not url:
            result.error = "vendor has no pricing_url"
            return result
        if self.offline:
            return self._from_snapshot(vendor, result)

        allowed, reason = self._robots_allowed(url)
        if not allowed:
            result.error = reason
            return result

        if self._requests_made and self.delay > 0:
            time.sleep(self.delay)
        self._requests_made += 1

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                if self.render:
                    result.html = self._render_page(url)
                    result.render_used = True
                else:
                    response = self._http().get(url)
                    result.status = response.status_code
                    response.raise_for_status()
                    result.html = response.text
                last_error = ""
                break
            except Exception as exc:  # network/HTTP errors are per-vendor, never fatal
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.max_retries:
                    time.sleep(float(2**attempt))

        if result.html:
            self._snapshot(vendor, result)
        else:
            result.error = last_error or "empty response"
        return result

    def latest_snapshot(self, vendor_id: str) -> Path | None:
        directory = self.data_dir / "snapshots" / vendor_id
        if not directory.is_dir():
            return None
        snapshots = sorted(directory.glob("*.html"), reverse=True)
        return snapshots[0] if snapshots else None

    def check_url(self, url: str) -> int | str:
        """Return an HTTP status for a catalog URL, or an error string.

        HEAD is polite for link checking; a 405/501 usually means the server
        dislikes HEAD, so fall back to a ranged GET before calling it broken.
        """
        if not url:
            return "no url"
        if self._requests_made and self.delay > 0:
            time.sleep(self.delay)
        self._requests_made += 1
        try:
            response = self._http().head(url)
            if response.status_code in (403, 405, 429, 501):
                response = self._http().get(url, headers={"Range": "bytes=0-1"})
            return response.status_code
        except Exception as exc:  # reported, never raised — catalog drift is data
            return f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ----------------------------------------------------------------- internal

    def _from_snapshot(self, vendor: Vendor, result: FetchResult) -> FetchResult:
        path = self.latest_snapshot(vendor.id)
        if path is None:
            result.error = "offline mode and no local snapshot for this vendor"
            return result
        result.html = path.read_text(encoding="utf-8", errors="replace")
        result.fetched_at = path.stem
        result.from_cache = True
        result.status = 200
        return result

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
                },
                follow_redirects=True,
                timeout=self.timeout,
            )
        return self._client

    def _robots_allowed(self, url: str) -> tuple[bool, str]:
        if self.ignore_robots:
            return True, ""
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                response = self._http().get(f"{host}/robots.txt")
                if response.status_code == 200 and "html" not in response.headers.get(
                    "content-type", ""
                ):
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(response.text.splitlines())
            except Exception:
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        if parser is None:
            return True, ""  # no readable robots.txt: treat as allowed
        if parser.can_fetch(USER_AGENT, url):
            return True, ""
        return False, f"robots.txt disallows {url}"

    def _render_page(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "--render needs Playwright: pip install 'coding-plan-radar[render]' "
                "then run `playwright install chromium`"
            ) from exc
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                page.wait_for_timeout(2000)  # let client-side pricing widgets settle
                return page.content()
            finally:
                browser.close()

    def _snapshot(self, vendor: Vendor, result: FetchResult) -> None:
        directory = self.data_dir / "snapshots" / vendor.id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{result.fetched_at}.html").write_text(result.html, encoding="utf-8")
        meta = {
            "vendor_id": vendor.id,
            "url": result.url,
            "status": result.status,
            "fetched_at": result.fetched_at,
            "render_used": result.render_used,
            "bytes": len(result.html.encode("utf-8")),
        }
        (directory / f"{result.fetched_at}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
