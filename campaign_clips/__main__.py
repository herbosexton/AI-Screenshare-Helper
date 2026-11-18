from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from campaign_clips.clipper import CampaignClipper
from campaign_clips.config import ConfigError, load_campaigns, twitch_credentials_from_env
from campaign_clips.monitor import CampaignMonitor
from campaign_clips.store import ClipStore
from campaign_clips.twitch_client import TwitchClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campaign_clips",
        description=(
            "Authorized campaign clip bots for contracted streamers. "
            "Requires Twitch clips:edit on partner channels and a valid campaign contract config."
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
        help="JSONL path for clip records / reporting",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Poll live partners and create clips")
    run_p.add_argument("--poll-interval", type=float, default=45.0)
    run_p.add_argument("--max-polls", type=int, default=None)
    run_p.add_argument(
        "--interval-fallback",
        action="store_true",
        help="Also clip on a fixed poll cadence (in addition to viewer spikes)",
    )
    run_p.add_argument("--interval-every", type=int, default=20)

    sub.add_parser("report", help="Print clip totals from the store")
    sub.add_parser("validate-config", help="Validate campaign YAML only")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    store = ClipStore(args.store)

    if args.command == "report":
        print(json.dumps(store.summarize(), indent=2))
        return 0

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
                },
                indent=2,
            )
        )
        return 0

    if args.command == "run":
        try:
            creds = twitch_credentials_from_env()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2

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
