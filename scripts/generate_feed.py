#!/usr/bin/env python3
"""Generate normalized podcast RSS and arXiv feeds.

This script intentionally uses only Python standard-library modules so the
central GitHub Actions workflow has no package-install step.
"""

from __future__ import annotations

import email.utils
import html
import json
import re
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "config" / "sources.json"
PODCAST_FEED_PATH = ROOT / "feeds" / "feed-podcasts.json"
ARXIV_FEED_PATH = ROOT / "feeds" / "feed-arxiv.json"

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


class FetchTimeoutError(TimeoutError):
    pass


def _timeout_handler(signum, frame):
    raise FetchTimeoutError("request timed out")


def fetch_text(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def fetch_channel(channel: dict[str, Any], cutoff: datetime, max_items: int) -> tuple[list[dict[str, Any]], str | None]:
    errors = []
    urls = [channel["rss_url"], *channel.get("fallback_rss_urls", [])]
    for url in urls:
        try:
            text = fetch_text(url)
            root = ET.fromstring(text)
            parsed_channel = {**channel, "rss_url": url}
            if root.tag.endswith("rss") or root.find("channel") is not None:
                return parse_rss(root, parsed_channel, cutoff, max_items), None
            if root.tag.endswith("feed"):
                return parse_atom(root, parsed_channel, cutoff, max_items), None
            errors.append(f"{url}: unknown feed format")
        except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as exc:
            errors.append(f"{url}: {exc}")
    return [], " | ".join(errors)


def write_podcast_feed(sources: dict[str, Any]) -> None:
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

    PODCAST_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PODCAST_FEED_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    print(f"Wrote {PODCAST_FEED_PATH} with {len(all_items)} items and {len(errors)} errors")


def arxiv_url(categories: list[dict[str, str]], max_results: int) -> str:
    query = " OR ".join(f"cat:{c['id']}" for c in categories)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(max_results),
    }
    return "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)


def authors_from_entry(entry: ET.Element) -> list[str]:
    authors = []
    for author in entry.findall("atom:author", NAMESPACES):
        name = first_text(author, ["atom:name"])
        if name:
            authors.append(name)
    return authors


def categories_from_entry(entry: ET.Element) -> list[str]:
    categories = []
    for category in entry.findall("atom:category", NAMESPACES):
        term = category.attrib.get("term", "")
        if term:
            categories.append(term)
    return categories


def parse_arxiv(root: ET.Element, cfg: dict[str, Any], cutoff: datetime) -> list[dict[str, Any]]:
    category_names = {c["id"]: c.get("name", c["id"]) for c in cfg.get("categories", [])}
    papers = []
    for entry in root.findall("atom:entry", NAMESPACES):
        published = parse_datetime(first_text(entry, ["atom:published", "atom:updated"]))
        if published and published < cutoff:
            continue
        paper_categories = categories_from_entry(entry)
        primary = paper_categories[0] if paper_categories else ""
        url = link_from_atom_entry(entry) or first_text(entry, ["atom:id"])
        title = first_text(entry, ["atom:title"])
        if not title and not url:
            continue
        papers.append(
            {
                "source": "arXiv",
                "title": title,
                "published_at": published.isoformat() if published else "",
                "updated_at": first_text(entry, ["atom:updated"]),
                "url": url,
                "summary": first_text(entry, ["atom:summary"]),
                "authors": authors_from_entry(entry),
                "categories": paper_categories,
                "primary_category": primary,
                "primary_category_name": category_names.get(primary, primary),
            }
        )
    return papers


def write_arxiv_feed(sources: dict[str, Any]) -> None:
    cfg = sources.get("arxiv", {})
    categories = cfg.get("categories", [])
    lookback_hours = int(cfg.get("lookback_hours", 48))
    max_papers = int(cfg.get("max_papers", 30))
    cutoff = utc_now() - timedelta(hours=lookback_hours)
    errors = []
    papers = []
    url = ""

    if categories:
        url = arxiv_url(categories, max_papers * 3)
        try:
            root = ET.fromstring(fetch_text(url))
            papers = parse_arxiv(root, cfg, cutoff)
        except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as exc:
            errors.append({"source": "arXiv", "url": url, "error": str(exc)})

    papers.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    papers = papers[:max_papers]
    output = {
        "generated_at": utc_now().isoformat(),
        "lookback_hours": lookback_hours,
        "category_count": len(categories),
        "categories": categories,
        "item_count": len(papers),
        "query_url": url,
        "papers": papers,
        "errors": errors,
    }

    ARXIV_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARXIV_FEED_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    print(f"Wrote {ARXIV_FEED_PATH} with {len(papers)} papers and {len(errors)} errors")


def main() -> int:
    sources = json.loads(SOURCES_PATH.read_text("utf-8"))
    write_podcast_feed(sources)
    write_arxiv_feed(sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
