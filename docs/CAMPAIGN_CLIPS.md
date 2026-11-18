# Authorized Campaign Clip Bots

Clip bots for **contracted** streamer campaigns (e.g. paid GTA 6 creator campaigns).

This module creates clips through the **Twitch Helix Clip API**. It does not scrape or download other people's VODs.

## Requirements

1. A written campaign / clip-rights agreement with each partner.
2. A Twitch app (`TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET`).
3. A **user access token** with `clips:edit` for each partner channel (you are the broadcaster, or an editor they added).
4. Optional `TWITCH_REFRESH_TOKEN` for long-running monitors.

## Setup

```bash
cp config/campaign_clips.example.yaml config/campaign_clips.yaml
# Edit partners: twitch_login, contract_id, game name, rate limits
```

Environment:

```bash
export TWITCH_CLIENT_ID=...
export TWITCH_CLIENT_SECRET=...
export TWITCH_ACCESS_TOKEN=...
export TWITCH_REFRESH_TOKEN=...   # optional
```

## Commands

```bash
# Validate partner/campaign YAML
python -m campaign_clips --config config/campaign_clips.yaml validate-config

# Run monitor (viewer-spike + game-match triggers)
python -m campaign_clips --config config/campaign_clips.yaml run --max-polls 20

# Also clip on a fixed cadence
python -m campaign_clips run --interval-fallback --interval-every 15

# Campaign report from stored clips
python -m campaign_clips report --store data/campaign_clips/clips.jsonl
```

## Behavior

- Polls contracted partners for live streams.
- Only clips when the stream game matches the campaign `game` field.
- Default moment trigger: viewer-count spike between polls.
- Enforces `max_clips_per_hour` per partner.
- Appends each clip to a JSONL store for payout / reporting.

## Not in scope

- Downloading full streams/VODs without platform APIs
- Clipping channels that are not in your contract config
- Bypassing Twitch auth / editor permissions
