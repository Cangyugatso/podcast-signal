# Podcast Signal

Podcast Signal is a minimal central RSS feed repository for agent-generated research digests.

It fetches podcast and YouTube RSS feeds on a schedule, writes a normalized JSON feed, and lets an agent read that JSON to produce a personalized digest.

## Feed

After GitHub Actions runs, the central feed is available at:

```text
https://raw.githubusercontent.com/<owner>/podcast-signal/main/feeds/feed-podcasts.json
```

CDN fallback:

```text
https://cdn.jsdelivr.net/gh/<owner>/podcast-signal@main/feeds/feed-podcasts.json
```

## Edit Sources

Edit `config/sources.json`.

Each channel needs:

- `name`
- `domain`
- `rss_url`

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
```

## GitHub Actions

The workflow runs daily at 22:00 UTC, which is 06:00 Beijing time.

You can also run it manually from:

```text
Actions -> Generate Podcast Feed -> Run workflow
```

