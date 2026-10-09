"""Synchronization CLI. Shares persistence, validation and locks with the API."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sync.service import SyncService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "check", "plan", "sync", "resume", "publish", "rollback", "build-battle", "verify-battle", "publish-battle", "backup", "restore", "cache-source"])
    parser.add_argument("--bundle")
    parser.add_argument("--destination")
    parser.add_argument("--commit")
    parser.add_argument("--job")
    parser.add_argument("--release")
    parser.add_argument("--home")
    parser.add_argument("--database")
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    service = SyncService(args.home, args.database)
    try:
        if args.command == "cache-source":
            if not args.commit:
                parser.error("cache-source requires --commit")
            result = service.source.cache_all(args.commit)
        elif args.command in ("backup", "restore"):
            from packages.sync.portable import export_bundle, restore_bundle
            if not args.bundle or (args.command == "restore" and not args.destination):
                parser.error("backup requires --bundle; restore also requires --destination")
            result = export_bundle(service, args.bundle) if args.command == "backup" else restore_bundle(args.bundle, args.destination)
        elif args.command == "status":
            result = service.status()
        elif args.command in ("check", "plan", "sync"):
            result = service.start(args.command, args.commit, background=False)
        elif args.command in ("build-battle", "verify-battle", "publish-battle"):
            if not args.job:
                parser.error("battle commands require --job")
            from packages.sync import verification
            if args.command == "build-battle":
                with service.jobs.lease():
                    result = {"releaseId": verification.build(service, args.job)}
            elif args.command == "verify-battle":
                result = verification.verify(service, args.job, args.games, args.workers)
            else:
                result = verification.publish(service, args.job, service.releases.current())
        elif args.command == "resume":
            if not args.job:
                parser.error("resume requires --job")
            service.run(args.job)
            result = service.jobs.get(args.job)
        elif args.command == "publish":
            if not args.job:
                parser.error("publish requires --job")
            with service.jobs.lease():
                service.publish(args.job)
            result = service.jobs.get(args.job)
        else:
            if not args.release:
                parser.error("rollback requires --release")
            result = service.rollback(args.release, service.releases.current())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("state") in ("failed", "blocked", "cancelled"):
            raise SystemExit(1)
    finally:
        service.close()


if __name__ == "__main__":
    main()
