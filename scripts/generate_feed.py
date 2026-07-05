#!/usr/bin/env python3
"""Generate a normalized podcast RSS feed.

This script intentionally uses only Python standard-library modules so the
central GitHub Actions workflow has no package-install step.
"""

from __future__ import annotations

import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "config" / "sources.json"
FEED_PATH = ROOT / "feeds" / "feed-podcasts.json"

USER_AGENT = "podcast-signal/0.1 (+https://github.com/)"
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def first_text(node: ET.Element, paths: list[str]) -> str:
    for path in paths:
        found = node.find(path, NAMESPACES)
        if found is not None and found.text:
            return clean_text(found.text)
    return ""


def link_from_rss_item(item: ET.Element) -> str:
    link = first_text(item, ["link"])
    if link:
        return link
    enclosure = item.find("enclosure")
    if enclosure is not None:
        return enclosure.attrib.get("url", "")
    return ""


def audio_from_rss_item(item: ET.Element) -> str:
    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = enclosure.attrib.get("url", "")
        content_type = enclosure.attrib.get("type", "")
        if url and ("audio" in content_type or url.endswith((".mp3", ".m4a", ".wav"))):
            return url
    return ""


def parse_rss(root: ET.Element, channel: dict[str, Any], cutoff: datetime, max_items: int) -> list[dict[str, Any]]:
    items = []
    for item in root.findall(".//item"):
        published = parse_datetime(first_text(item, ["pubDate", "{http://purl.org/dc/elements/1.1/}date"]))
        if published and published < cutoff:
            continue
        url = link_from_rss_item(item)
        title = first_text(item, ["title"])
        if not title and not url:
            continue
        items.append(
            {
                "source": channel["name"],
                "domain": channel.get("domain", ""),
                "language": channel.get("language", ""),
                "title": title,
                "published_at": published.isoformat() if published else "",
                "url": url,
                "summary": first_text(item, ["description", "itunes:summary", "content:encoded"]),
                "audio_url": audio_from_rss_item(item),
                "feed_url": channel["rss_url"],
                "source_type": "rss",
            }
        )
    return sorted(items, key=lambda x: x.get("published_at") or "", reverse=True)[:max_items]


def link_from_atom_entry(entry: ET.Element) -> str:
    for link in entry.findall("atom:link", NAMESPACES):
        rel = link.attrib.get("rel", "alternate")
        href = link.attrib.get("href", "")
        if href and rel == "alternate":
            return href
    for link in entry.findall("atom:link", NAMESPACES):
        href = link.attrib.get("href", "")
        if href:
            return href
    return ""


def parse_atom(root: ET.Element, channel: dict[str, Any], cutoff: datetime, max_items: int) -> list[dict[str, Any]]:
    items = []
    for entry in root.findall("atom:entry", NAMESPACES):
        published = parse_datetime(first_text(entry, ["atom:published", "atom:updated"]))
        if published and published < cutoff:
            continue
        url = link_from_atom_entry(entry)
        title = first_text(entry, ["atom:title"])
        if not title and not url:
            continue
        video_id = first_text(entry, ["yt:videoId"])
        items.append(
            {
                "source": channel["name"],
                "domain": channel.get("domain", ""),
                "language": channel.get("language", ""),
                "title": title,
                "published_at": published.isoformat() if published else "",
                "url": url,
                "summary": first_text(entry, ["atom:summary", "media:group/media:description"]),
                "audio_url": "",
                "feed_url": channel["rss_url"],
                "source_type": "youtube" if video_id else "atom",
                "video_id": video_id,
            }
        )
    return sorted(items, key=lambda x: x.get("published_at") or "", reverse=True)[:max_items]


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_channel(channel: dict[str, Any], cutoff: datetime, max_items: int) -> tuple[list[dict[str, Any]], str | None]:
    try:
        text = fetch_text(channel["rss_url"])
        root = ET.fromstring(text)
        if root.tag.endswith("rss") or root.find("channel") is not None:
            return parse_rss(root, channel, cutoff, max_items), None
        if root.tag.endswith("feed"):
            return parse_atom(root, channel, cutoff, max_items), None
        return [], "unknown feed format"
    except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as exc:
        return [], str(exc)


def main() -> int:
    sources = json.loads(SOURCES_PATH.read_text("utf-8"))
    cfg = sources.get("podcasts", {})
    lookback_hours = int(cfg.get("lookback_hours", 168))
    max_items = int(cfg.get("max_items_per_channel", 5))
    cutoff = utc_now() - timedelta(hours=lookback_hours)

    all_items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for channel in cfg.get("channels", []):
        items, error = fetch_channel(channel, cutoff, max_items)
        all_items.extend(items)
        if error:
            errors.append({"source": channel.get("name", ""), "url": channel.get("rss_url", ""), "error": error})

    all_items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    output = {
        "generated_at": utc_now().isoformat(),
        "lookback_hours": lookback_hours,
        "source_count": len(cfg.get("channels", [])),
        "item_count": len(all_items),
        "podcasts": all_items,
        "errors": errors,
    }

    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEED_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    print(f"Wrote {FEED_PATH} with {len(all_items)} items and {len(errors)} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())

