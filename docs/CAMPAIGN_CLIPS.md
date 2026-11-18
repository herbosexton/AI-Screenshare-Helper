# Multi-site Rewards Clip + Upload Bots

For contracted streamer campaigns paid through **Content Rewards and similar websites**.

Those portals usually have **no public API**, so the bot:

1. Watches **live** contracted partners on **Twitch and/or YouTube**, and/or processes authorized VODs
2. Cuts highlight clips (streamlink record window + optional Twitch Helix create-clip)
3. Logs into each configured rewards website with your campaign accounts
4. Uploads vertical clips through each site’s UI (Playwright)
5. Tracks per-site status: `ready` → `submitted` → `paid`

## Setup

```bash
cp config/campaign_clips.example.yaml config/campaign_clips.yaml
# Fill reward_sites.* URLs + selectors for every portal you get paid on
# Map each campaign.reward_sites list to those site ids

export CONTENT_REWARDS_EMAIL='...'
export CONTENT_REWARDS_PASSWORD='...'
export CLIP_REWARDS_EMAIL='...'      # example second site
export CLIP_REWARDS_PASSWORD='...'

# Live monitoring (contracted partners)
export TWITCH_CLIENT_ID='...'
export TWITCH_CLIENT_SECRET='...'
export TWITCH_ACCESS_TOKEN='...'     # user token with clips:edit when using Helix
export YOUTUBE_API_KEY='...'         # YouTube Data API v3 key for live checks

pip install playwright streamlink
playwright install chromium
```

## Commands

```bash
python -m campaign_clips validate-config

# LIVE — watch Twitch + YouTube partners, cut clips, queue uploads
python -m campaign_clips live --max-polls 20
python -m campaign_clips live --auto-upload --interval-every 6

# VOD inbox fallback
python -m campaign_clips process \
  --campaign gta6-launch-week \
  --partner partner_streamer_one \
  --source /path/to/authorized_vod.mp4

# Upload ready clips to every site on the campaign
python -m campaign_clips upload --campaign gta6-launch-week

# Or one site only while tuning selectors
python -m campaign_clips upload --campaign gta6-launch-week --site content_rewards --headed --limit 1

python -m campaign_clips tracker list
```

## Adding another rewards website

1. Add a new key under `reward_sites:` with URL + selectors + credential env var names
2. Append that site id to the campaign’s `reward_sites:` list
3. Set `site_slugs:` if the campaign path differs per site
4. Set the env vars and run `upload`

## Not in scope

- Downloading streams/VODs you do not have rights to
- Bypassing CAPTCHA / 2FA / account security controls
