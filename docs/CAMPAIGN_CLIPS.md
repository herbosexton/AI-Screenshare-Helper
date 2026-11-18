# Content Rewards Clip System

Clip system for **contracted** streamer campaigns when the payout surface is **Content Rewards** (no public API).

## How it works

1. Partners under contract provide authorized VODs/recordings (or you capture with their permission).
2. Drop files into an inbox folder.
3. The bot detects highlight windows, cuts clips, and exports **9:16 vertical** files + captions.
4. You upload those clips manually in the Content Rewards portal.
5. Mark each clip `submitted` / `paid` in the local tracker (CSV/JSONL).

Optional: Twitch Helix `twitch-run` if you also have editor/`clips:edit` on partner channels.

## Setup

```bash
cp config/campaign_clips.example.yaml config/campaign_clips.yaml
mkdir -p data/campaign_clips/inbox/partner_streamer_one
# Copy authorized recordings into that inbox
```

## Commands

```bash
# Validate config
python -m campaign_clips --config config/campaign_clips.yaml validate-config

# Process one authorized file
python -m campaign_clips process \
  --campaign gta6-launch-week \
  --partner partner_streamer_one \
  --source /path/to/authorized_vod.mp4

# Process a partner inbox folder
python -m campaign_clips process \
  --campaign gta6-launch-week \
  --partner partner_streamer_one

# Tracker (manual Content Rewards statuses)
python -m campaign_clips tracker list
python -m campaign_clips tracker mark --clip-id ID --status submitted --url 'https://...'
python -m campaign_clips tracker csv --out data/campaign_clips/content_rewards_tracker.csv
```

## Output package

For each source:

- `horizontal/*.mp4` — landscape cuts
- `vertical/*_9x16.mp4` — Content Rewards / Shorts-ready
- `meta/*.json` — per-clip metadata
- `captions.txt` — paste-friendly captions
- `manifest.json` — full export manifest

## Not in scope

- Scraping or automating the Content Rewards website login/upload
- Downloading streams/VODs you do not have rights to
- Bypassing platform auth
