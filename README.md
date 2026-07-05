# Podcast Signal

Podcast Signal is a minimal central RSS/arXiv feed repository for agent-generated research digests.

It fetches podcast RSS, YouTube RSS, and arXiv feeds on a schedule, writes normalized JSON feeds, and lets an agent read those JSON files to produce a personalized digest.

## Feed

After GitHub Actions runs, the central feed is available at:

```text
https://raw.githubusercontent.com/<owner>/podcast-signal/main/feeds/feed-podcasts.json
```

```text
https://raw.githubusercontent.com/<owner>/podcast-signal/main/feeds/feed-arxiv.json
```

CDN fallback:

```text
https://cdn.jsdelivr.net/gh/<owner>/podcast-signal@main/feeds/feed-podcasts.json
```

```text
https://cdn.jsdelivr.net/gh/<owner>/podcast-signal@main/feeds/feed-arxiv.json
```

## Edit Sources

Edit `config/sources.json`.

Each podcast channel needs:

- `name`
- `domain`
- `rss_url`

arXiv categories live under `arxiv.categories`.

Optional fields:

- `language`
- `notes`

## Run Locally

```bash
python3 scripts/generate_feed.py
```

The script writes:

```text
feeds/feed-podcasts.json
feeds/feed-arxiv.json
```

## GitHub Actions

The workflow runs daily at 22:00 UTC, which is 06:00 Beijing time.

You can also run it manually from:

```text
Actions -> Generate Podcast Feed -> Run workflow
```
