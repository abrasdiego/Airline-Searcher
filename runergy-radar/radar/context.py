"""Reads the page a conversation is on, so the AI sees the whole post and the replies already
there instead of only a search snippet. Best effort: sites that refuse are simply skipped."""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from radar.sources import USER_AGENT

MAX_BYTES = 1_500_000
MAX_TEXT = 7000
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "form", "aside", "svg", "iframe",
         "button", "select", "template"}
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "blockquote", "article",
          "section", "td", "pre"}


def is_public_host(host: str) -> bool:
    """False for addresses inside a private network, so a crafted link can't reach internal systems."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return False
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
                or address.is_multicast or address.is_unspecified):
            return False
    return bool(infos)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main: list[str] = []
        self.skip = 0
        self.inside_main = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        if tag in ("main", "article"):
            self.inside_main += 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag in ("main", "article") and self.inside_main:
            self.inside_main -= 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_data(self, data):
        if not self.skip:
            self._add(data)

    def _add(self, text):
        self.parts.append(text)
        if self.inside_main:
            self.main.append(text)


def _tidy(parts: list[str]) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parts))
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if len(line) > 1)


def html_to_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:                    # malformed markup: keep whatever was read
        pass
    main, everything = _tidy(parser.main), _tidy(parser.parts)
    text = main if len(main) >= 400 else everything
    return text[:MAX_TEXT]


def read_page(url: str, timeout: float = 8, allow_private: bool = False) -> str:
    """The readable text of a public page, or '' if it can't be read."""
    for _ in range(4):                   # follow a few redirects, checking each stop
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        if not allow_private and not is_public_host(parts.hostname):
            return ""
        try:
            response = requests.get(url, timeout=timeout, stream=True, allow_redirects=False,
                                    headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        except requests.RequestException:
            return ""
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
            url = urljoin(url, response.headers["Location"])
            response.close()
            continue
        if response.status_code != 200 or "html" not in response.headers.get("Content-Type", "").lower():
            response.close()
            return ""
        try:
            body = response.raw.read(MAX_BYTES, decode_content=True)
        except Exception:
            return ""
        finally:
            response.close()
        return html_to_text(body.decode(response.encoding or "utf-8", errors="replace"))
    return ""
