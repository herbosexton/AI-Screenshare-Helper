# Content Rewards Clip + Portal Upload Bots

Clip system for **contracted** streamer campaigns paid through **Content Rewards**.

Content Rewards has **no public API**, so the bot:

1. Cuts clips from authorized partner VODs
2. **Logs into the Content Rewards website** with your campaign account
3. **Uploads** the vertical clips through the portal UI (Playwright)
4. Marks each clip `submitted` in the local tracker

## Setup

```bash
cp config/campaign_clips.example.yaml config/campaign_clips.yaml
# Fill in portal.base_url / login_url / upload_url_template + CSS selectors
# Add contracted partners

export CONTENT_REWARDS_EMAIL='your-campaign-login'
export CONTENT_REWARDS_PASSWORD='...'

pip install playwright
playwright install chromium
```

## Commands

```bash
python -m campaign_clips validate-config

# Cut clips from an authorized VOD
python -m campaign_clips process \
  --campaign gta6-launch-week \
  --partner partner_streamer_one \
  --source /path/to/authorized_vod.mp4

# Bot uploads every tracker row still marked ready
python -m campaign_clips upload --campaign gta6-launch-week

# Debug selectors with a visible browser
python -m campaign_clips upload --campaign gta6-launch-week --headed --limit 1

python -m campaign_clips tracker list
```

## Portal selectors

Each Content Rewards site has different HTML. Put the real CSS/text selectors under `portal.selectors` in your YAML. Use `--headed --limit 1` while tuning.

## Credentials

Use env vars only (never commit passwords):

- `CONTENT_REWARDS_EMAIL`
- `CONTENT_REWARDS_PASSWORD`

## Not in scope

- Downloading streams/VODs you do not have rights to
- Bypassing CAPTCHA / 2FA / account security controls
