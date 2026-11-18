from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from campaign_clips.config import ConfigError, load_campaigns, twitch_credentials_from_env
from campaign_clips.pipeline import find_campaign, find_partner, process_inbox, process_source
from campaign_clips.store import ClipStore
from campaign_clips.tracker import ContentRewardsTracker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campaign_clips",
        description=(
            "Content Rewards clip system for contracted streamers. "
            "Primary path is local authorized VOD → highlights → vertical export + tracker "
            "(Content Rewards has no API). Optional Twitch Helix clipping remains available."
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

    track_p = sub.add_parser("tracker", help="Content Rewards submission tracker (manual statuses)")
    track_sub = track_p.add_subparsers(dest="tracker_command", required=True)
    track_sub.add_parser("list", help="Show tracker summary")
    mark_p = track_sub.add_parser("mark", help="Update a clip status")
    mark_p.add_argument("--clip-id", required=True)
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

    run_p = sub.add_parser(
        "twitch-run",
        help="Optional: poll live Twitch partners via Helix Clip API (requires clips:edit)",
    )
    run_p.add_argument("--poll-interval", type=float, default=45.0)
    run_p.add_argument("--max-polls", type=int, default=None)
    run_p.add_argument("--interval-fallback", action="store_true")
    run_p.add_argument("--interval-every", type=int, default=20)

    sub.add_parser("twitch-report", help="Print optional Twitch clip store totals")

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
        print(
            json.dumps(
                {
                    "ok": True,
                    "campaigns": len(campaigns),
                    "enabled_campaigns": enabled,
                    "partners": partners,
                    "mode": "content_rewards_local_export",
                },
                indent=2,
            )
        )
        return 0

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
                        "Upload vertical/*.mp4 in the Content Rewards portal, "
                        "then: python -m campaign_clips tracker mark --clip-id ID --status submitted"
                    ),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "twitch-run":
        try:
            creds = twitch_credentials_from_env()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        from campaign_clips.clipper import CampaignClipper
        from campaign_clips.monitor import CampaignMonitor
        from campaign_clips.twitch_client import TwitchClient

        store = ClipStore(args.store)
        with TwitchClient(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            access_token=creds["access_token"],
            refresh_token=creds.get("refresh_token"),
        ) as client:
            clipper = CampaignClipper(client, store)

            def on_clip(record):  # type: ignore[no-untyped-def]
                print(
                    f"[clip] {record.campaign_id} @{record.partner_login} "
                    f"{record.clip_id} ({record.trigger}) {record.url}"
                )

            def on_skip(reason: str) -> None:
                print(f"[skip] {reason}")

            monitor = CampaignMonitor(
                campaigns,
                clipper,
                poll_interval_s=args.poll_interval,
                use_viewer_spike=True,
                use_interval_fallback=bool(args.interval_fallback),
                interval_every_n_polls=args.interval_every,
                on_clip=on_clip,
                on_skip=on_skip,
            )
            stats = monitor.run(max_polls=args.max_polls)
            print(
                json.dumps(
                    {
                        "polls": stats.polls,
                        "live_sightings": stats.live_sightings,
                        "clips_created": stats.clips_created,
                        "skipped": stats.skipped,
                    },
                    indent=2,
                )
            )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
