from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from campaign_clips.config import ConfigError, load_campaigns, twitch_credentials_from_env
from campaign_clips.live import LiveCampaignWorker
from campaign_clips.pipeline import find_campaign, find_partner, process_inbox, process_source
from campaign_clips.portal import upload_campaign_to_sites
from campaign_clips.portal_config import load_reward_sites
from campaign_clips.store import ClipStore
from campaign_clips.tracker import ContentRewardsTracker
from campaign_clips.twitch_client import TwitchClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campaign_clips",
        description=(
            "Rewards clip bots for contracted streamers: live Twitch monitoring + VOD inbox, "
            "highlight cuts, then Playwright upload to Content Rewards and similar sites."
        ),
    )
    parser.add_argument(
        "--config",
        default="config/campaign_clips.yaml",
        help="Path to campaign YAML (see config/campaign_clips.example.yaml)",
    )
    parser.add_argument(
        "--store",
        default="data/campaign_clips/clips.jsonl",
        help="JSONL path for optional Twitch clip records",
    )
    parser.add_argument(
        "--tracker",
        default="data/campaign_clips/content_rewards_tracker.jsonl",
        help="JSONL tracker for Content Rewards upload status",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-config", help="Validate campaign YAML only")

    process_p = sub.add_parser(
        "process",
        help="Process an authorized local VOD/recording into Content Rewards-ready clips",
    )
    process_p.add_argument("--campaign", required=True, help="Campaign id from YAML")
    process_p.add_argument("--partner", required=True, help="Partner twitch_login / handle")
    process_p.add_argument("--source", help="Path to one authorized video file")
    process_p.add_argument(
        "--inbox",
        help="Process all videos in this folder (defaults to campaign.inbox_dir/<partner>)",
    )
    process_p.add_argument("--max-clips", type=int, default=None)
    process_p.add_argument("--clip-duration", type=float, default=None)
    process_p.add_argument(
        "--no-vertical",
        action="store_true",
        help="Skip 9:16 export (horizontal only)",
    )

    upload_p = sub.add_parser(
        "upload",
        help="Bot-login to configured rewards websites and upload ready clips",
    )
    upload_p.add_argument("--campaign", required=True, help="Campaign id from YAML")
    upload_p.add_argument(
        "--site",
        action="append",
        default=None,
        help="Reward site id to upload to (repeatable). Default: all sites on the campaign",
    )
    upload_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max clips to upload per site this run",
    )
    upload_p.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (useful when tuning selectors)",
    )

    track_p = sub.add_parser("tracker", help="Content Rewards submission tracker")
    track_sub = track_p.add_subparsers(dest="tracker_command", required=True)
    track_sub.add_parser("list", help="Show tracker summary")
    mark_p = track_sub.add_parser("mark", help="Update a clip status")
    mark_p.add_argument("--clip-id", required=True)
    mark_p.add_argument("--site", default="content_rewards", help="Reward site id")
    mark_p.add_argument(
        "--status",
        required=True,
        choices=["ready", "submitted", "rejected", "paid"],
    )
    mark_p.add_argument("--url", default="", help="External post URL after upload")
    mark_p.add_argument("--notes", default="")
    csv_p = track_sub.add_parser("csv", help="Export tracker to CSV")
    csv_p.add_argument(
        "--out",
        default="data/campaign_clips/content_rewards_tracker.csv",
    )

    live_p = sub.add_parser(
        "live",
        help="Watch contracted partners live, cut clips, enqueue rewards uploads",
    )
    live_p.add_argument("--poll-interval", type=float, default=45.0)
    live_p.add_argument("--max-polls", type=int, default=None)
    live_p.add_argument("--record-duration", type=float, default=None)
    live_p.add_argument(
        "--interval-every",
        type=int,
        default=8,
        help="Also clip every N polls while live on the campaign game",
    )
    live_p.add_argument(
        "--no-twitch-clip-api",
        action="store_true",
        help="Skip Helix create-clip; only record local live windows",
    )
    live_p.add_argument(
        "--auto-upload",
        action="store_true",
        help="After each live clip batch, upload ready rows to configured reward sites",
    )
    live_p.add_argument(
        "--headed",
        action="store_true",
        help="Show browser when --auto-upload is set",
    )

    sub.add_parser("twitch-report", help="Print Twitch clip store totals")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "twitch-report":
        print(json.dumps(ClipStore(args.store).summarize(), indent=2))
        return 0

    if args.command == "tracker":
        tracker = ContentRewardsTracker(args.tracker)
        if args.tracker_command == "list":
            print(json.dumps(tracker.summarize(), indent=2))
            return 0
        if args.tracker_command == "csv":
            path = tracker.export_csv(args.out)
            print(json.dumps({"ok": True, "csv": str(path)}, indent=2))
            return 0
        if args.tracker_command == "mark":
            try:
                row = tracker.set_status(
                    args.clip_id,
                    args.status,
                    site_id=args.site,
                    external_post_url=args.url,
                    notes=args.notes,
                )
            except (KeyError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(json.dumps(row.to_dict(), indent=2))
            return 0
        parser.error(f"Unknown tracker command: {args.tracker_command}")
        return 2

    config_path = Path(args.config)
    try:
        campaigns = load_campaigns(config_path)
    except (OSError, ConfigError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate-config":
        enabled = sum(1 for c in campaigns if c.enabled)
        partners = sum(len(c.partners) for c in campaigns)
        try:
            registry = load_reward_sites(config_path)
        except ConfigError as exc:
            print(f"Config error: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "ok": True,
                    "campaigns": len(campaigns),
                    "enabled_campaigns": enabled,
                    "partners": partners,
                    "reward_sites": registry.ids(),
                    "mode": "multi_site_rewards_portal_bot",
                },
                indent=2,
            )
        )
        return 0

    if args.command == "upload":
        try:
            campaign = find_campaign(campaigns, args.campaign)
            registry = load_reward_sites(config_path)
        except (KeyError, ConfigError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not registry.sites:
            print(
                "Config error: configure reward_sites: (or legacy portal:) "
                "with base_url, login_url, upload_url_template.",
                file=sys.stderr,
            )
            return 2

        tracker = ContentRewardsTracker(args.tracker)
        try:
            results = upload_campaign_to_sites(
                campaign=campaign,
                registry=registry,
                tracker=tracker,
                site_ids=args.site,
                limit_per_site=args.limit,
                headed=bool(args.headed),
            )
        except Exception as exc:
            print(f"Upload error: {exc}", file=sys.stderr)
            return 2

        print(
            json.dumps(
                {
                    "uploaded": sum(1 for r in results if r.ok),
                    "failed": sum(1 for r in results if not r.ok),
                    "results": [
                        {
                            "clip_id": r.clip_id,
                            "site_id": r.site_id,
                            "ok": r.ok,
                            "post_url": r.post_url,
                            "error": r.error,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
        return 0 if all(r.ok for r in results) or not results else 1

    if args.command == "process":
        try:
            campaign = find_campaign(campaigns, args.campaign)
            partner = find_partner(campaign, args.partner)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        tracker = ContentRewardsTracker(args.tracker)
        out_root = campaign.export_dir
        make_vertical = not args.no_vertical

        try:
            if args.source:
                result = process_source(
                    source=args.source,
                    campaign=campaign,
                    partner=partner,
                    out_root=out_root,
                    tracker=tracker,
                    max_clips=args.max_clips,
                    clip_duration_s=args.clip_duration,
                    make_vertical=make_vertical,
                )
                results = [result]
            else:
                inbox = args.inbox
                if not inbox:
                    base = campaign.inbox_dir or "data/campaign_clips/inbox"
                    inbox = str(Path(base) / partner.normalized_login())
                results = process_inbox(
                    inbox_dir=inbox,
                    campaign=campaign,
                    partner=partner,
                    out_root=out_root,
                    tracker=tracker,
                    max_clips=args.max_clips,
                    make_vertical=make_vertical,
                )
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            print(f"Process error: {exc}", file=sys.stderr)
            return 2

        print(
            json.dumps(
                {
                    "processed_sources": len(results),
                    "clips": sum(len(r.clips) for r in results),
                    "outputs": [
                        {
                            "source": r.source,
                            "out_dir": r.out_dir,
                            "clip_ids": [c.clip_id for c in r.clips],
                        }
                        for r in results
                    ],
                    "next": (
                        "python -m campaign_clips upload "
                        f"--campaign {campaign.id}"
                    ),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "live":
        try:
            creds = twitch_credentials_from_env()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        tracker = ContentRewardsTracker(args.tracker)
        registry = load_reward_sites(config_path)

        def on_event(event) -> None:  # type: ignore[no-untyped-def]
            print(
                f"[live] {event.campaign_id} @{event.partner_login} "
                f"{event.method} {event.trigger} -> {event.clip_ids}"
            )
            if args.auto_upload:
                try:
                    campaign = find_campaign(campaigns, event.campaign_id)
                    upload_results = upload_campaign_to_sites(
                        campaign=campaign,
                        registry=registry,
                        tracker=tracker,
                        headed=bool(args.headed),
                    )
                    print(
                        json.dumps(
                            {
                                "auto_upload": True,
                                "uploaded": sum(1 for r in upload_results if r.ok),
                                "failed": sum(1 for r in upload_results if not r.ok),
                            }
                        )
                    )
                except Exception as exc:
                    print(f"[live] auto-upload error: {exc}", file=sys.stderr)

        def on_skip(reason: str) -> None:
            print(f"[skip] {reason}")

        def on_error(reason: str) -> None:
            print(f"[error] {reason}", file=sys.stderr)

        with TwitchClient(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            access_token=creds["access_token"],
            refresh_token=creds.get("refresh_token"),
        ) as client:
            worker = LiveCampaignWorker(
                campaigns,
                client,
                tracker=tracker,
                poll_interval_s=args.poll_interval,
                record_duration_s=args.record_duration,
                use_viewer_spike=True,
                use_interval_fallback=True,
                interval_every_n_polls=args.interval_every,
                prefer_twitch_clip_api=not args.no_twitch_clip_api,
                on_event=on_event,
                on_skip=on_skip,
                on_error=on_error,
            )
            stats = worker.run(max_polls=args.max_polls)
            print(
                json.dumps(
                    {
                        "polls": stats.polls,
                        "live_sightings": stats.live_sightings,
                        "clips_made": stats.clips_made,
                        "skipped": stats.skipped,
                        "errors": stats.errors,
                    },
                    indent=2,
                )
            )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
