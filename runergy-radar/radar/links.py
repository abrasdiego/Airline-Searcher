"""The link that goes into replies: one place decides it, so it can be changed at any time.

Two ways to link:
- directly to your address (https://runergylab.com), or
- through Radar (https://go.runergylab.com/go/ab12cd34ef), which counts clicks per reply and
  always forwards to whatever your address is today, so replies posted months ago follow you to
  the App Store when you change it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from radar.defaults import community_rules

MODES = ("when_mentioned", "always", "never")
PLATFORM_TAGS = {"reddit": "reddit", "threads": "threads", "bluesky": "bluesky",
                 "facebook": "facebook", "web": "forum"}
CODE_LENGTH = 10
_PATH = r"(?:/[^\s<>\"')\]]*)?"
_TRAILING = ".,;:!?"
_BOTS = re.compile(r"bot|crawl|spider|preview|fetch|scan|monitor|facebookexternalhit|slack|discord|"
                   r"whatsapp|telegram|curl|wget|python|headless|embed", re.I)


def short_base(settings: dict) -> str:
    """The web address counted links start with ('' when unknown)."""
    return settings["link"]["base"] or settings.get("_base", "")


def uses_radar(settings: dict) -> bool:
    return bool(settings["link"]["via_radar"] and settings["brand"]["url"] and short_base(settings))


def destination(settings: dict, platform: str = "") -> str:
    """Where a reader ends up: your address, tagged for analytics if you asked for that."""
    base = settings["brand"]["url"]
    if not base or not settings["link"]["track"] or platform not in PLATFORM_TAGS:
        return base
    parts = urlparse(base)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("utm_source", "utm_medium", "utm_campaign")]
    query += [("utm_source", PLATFORM_TAGS[platform]), ("utm_medium", "community"),
              ("utm_campaign", "radar")]
    return urlunparse(parts._replace(query=urlencode(query)))


def reply_link(settings: dict, platform: str = "", thread_id: str = "") -> str:
    """The link to put in a reply ('' when no link is set)."""
    if not settings["brand"]["url"]:
        return ""
    if thread_id and uses_radar(settings):
        return f"{short_base(settings)}/go/{thread_id[:CODE_LENGTH]}"
    return destination(settings, platform)


def link_mode(settings: dict, community: str = "") -> str:
    """'never' for communities you marked as not allowing links."""
    if community_rules(settings, community).get("no_links"):
        return "never"
    return settings["link"]["mode"]


def is_person(user_agent: str) -> bool:
    """False for link-preview robots and crawlers, so they don't count as clicks."""
    return bool(user_agent) and not _BOTS.search(user_agent)


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(host: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@/.\-])(?:https?://)?(?:www\.)?{re.escape(host)}{_PATH}", re.I)


def enforce(draft: str, settings: dict, platform: str, thread_id: str = "", community: str = "") -> str:
    """Make an AI-written reply carry exactly the chosen link, exactly as Settings says."""
    link = reply_link(settings, platform, thread_id)
    mode = link_mode(settings, community)
    if not draft.strip() or not link:
        return draft

    def swap(match: re.Match) -> str:
        found = match.group(0)
        tail = ""
        while found and found[-1] in _TRAILING:
            found, tail = found[:-1], found[-1] + tail
        return ("" if mode == "never" else link) + tail

    hosts = {_host(link), _host(settings["brand"]["url"])}
    for host in sorted(hosts, key=len, reverse=True):
        draft = _same_site(host).sub(swap, draft)
    if mode == "never":
        return re.sub(r"[ \t]+\n", "\n", draft).strip()
    mentioned = settings["brand"]["name"].lower() in draft.lower()
    if link not in draft and (mode == "always" or mentioned):
        draft = draft.rstrip() + "\n\n" + link
    return draft


def replace_link(draft: str, old: str, new: str) -> str:
    """Swap one exact link for another, leaving longer links that merely start the same."""
    if not old or old == new or old not in draft:
        return draft
    pattern = re.compile(re.escape(old.rstrip("/")) + r"/?(?![\w/?=&%#\-])")
    return pattern.sub(lambda _: new, draft)


def relink_drafts(store, before: dict, after: dict) -> int:
    """After the link changes, update it in every reply that hasn't been posted. Returns how many."""
    if not before["brand"]["url"] or not after["brand"]["url"]:
        return 0
    hosts = {_host(before["brand"]["url"])}
    if uses_radar(before):
        hosts.add(_host(short_base(before)))
    seen, changed = set(), 0
    for host in hosts:
        for thread in store.drafts_containing(host):
            if thread["id"] in seen:
                continue
            seen.add(thread["id"])
            new = reply_link(after, thread["platform"], thread["id"])
            olds = {reply_link(before, thread["platform"], thread["id"]),
                    destination(before, thread["platform"]), before["brand"]["url"]}
            draft = thread["draft"]
            for old in sorted(olds, key=len, reverse=True):      # longest first: it contains the others
                draft = replace_link(draft, old, new)
            if draft != thread["draft"]:
                store.update_thread(thread["id"], draft=draft)
                changed += 1
    return changed
