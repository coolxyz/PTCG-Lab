"""Consistent private PostgreSQL backups; restore only into an empty deployment."""

from datetime import datetime, timezone
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import subprocess


def command(root, report, *args):
    return ["docker", "compose", "--project-name", report["project"], "-f", str(Path(root) / "compose.yaml"), *args]


def run(root, report, *args, **kwargs):
    result = subprocess.run(command(root, report, *args), cwd=root, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise ValueError("Database operation failed; original backup and existing volumes retained")
    return result


def scalar(root, report, sql):
    return run(root, report, "exec", "-T", "database", "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "ptcg_v2", "-d", "ptcg_v2", "-c", sql, stdout=subprocess.PIPE).stdout.decode().strip()


def safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Backup paths must not use symbolic links")
    return path


def backup(root, report, output):
    output = safe(output)
    server = scalar(root, report, "SELECT server_id FROM ptcg_v2.deployment WHERE singleton")
    if server != report.get("serverId"):
        raise ValueError("Database identity differs from this kit")
    output.mkdir(mode=0o700)
    dump = output / "database.dump"
    try:
        with dump.open("xb") as stream:
            os.chmod(dump, 0o600)
            run(root, report, "exec", "-T", "database", "pg_dump", "-U", "ptcg_v2", "-d", "ptcg_v2", "--format=custom", "--no-owner", "--no-privileges", stdout=stream)
            stream.flush()
            os.fsync(stream.fileno())
        with dump.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        metadata = {
            "schema": 1, "kind": "ptcg-private-database-backup", "createdAt":datetime.now(timezone.utc).isoformat(),
            "origin":report["origin"], "serverId":server, "ruleReleaseId":report["ruleReleaseId"],
            "postgresMajor":17, "dumpSha256":digest,
            "containsCredentialsAndPrivateData":True,
        }
        path = output / "BACKUP.json"
        with path.open("x") as stream:
            os.chmod(path, 0o600)
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        fingerprint = sha256(path.read_bytes()).hexdigest()
        (output / "BACKUP.json.sha256").write_text(fingerprint + "  BACKUP.json\n")
        return metadata
    except BaseException:
        # Leave an incomplete backup clearly without a successful metadata file.
        # Never clean up another backup or database as part of error recovery.
        raise


def verify_backup(directory, report, expected_sha):
    directory = safe(directory)
    descriptor, dump = safe(directory / "BACKUP.json"), safe(directory / "database.dump")
    if not descriptor.is_file() or not dump.is_file() or descriptor.stat().st_size > 16384:
        raise ValueError("Incomplete backup")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64 or sha256(descriptor.read_bytes()).hexdigest() != expected_sha:
        raise ValueError("Backup descriptor does not match the trusted SHA-256")
    value = json.loads(descriptor.read_bytes())
    if value.get("schema") != 1 or value.get("kind") != "ptcg-private-database-backup" or value.get("postgresMajor") != 17:
        raise ValueError("Unsupported backup")
    for field in ("origin", "serverId", "ruleReleaseId"):
        if value.get(field) != report.get(field):
            raise ValueError("Restore kit must match the backup's origin, server ID and current rule release")
    with dump.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != value.get("dumpSha256"):
            raise ValueError("Database dump checksum mismatch")
    return value


def restore(root, report, directory, expected_sha):
    verify_backup(directory, report, expected_sha)
    running = run(root, report, "ps", "--status", "running", "--quiet", "api", stdout=subprocess.PIPE).stdout
    if running.strip():
        raise ValueError("Stop only this kit's API before restoring")
    run(root, report, "up", "--detach", "--wait", "database", stdout=subprocess.DEVNULL)
    objects = scalar(root, report, "SELECT (SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%') + (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')")
    if objects != "0":
        raise ValueError("Restore requires an empty database; existing data is never overwritten")
    with (Path(directory) / "database.dump").open("rb") as stream:
        run(root, report, "exec", "-T", "database", "pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges", "-U", "ptcg_v2", "-d", "ptcg_v2", stdin=stream, stdout=subprocess.DEVNULL)
    server = scalar(root, report, "SELECT server_id FROM ptcg_v2.deployment WHERE singleton")
    if server != report["serverId"]:
        raise ValueError("Restored server identity mismatch; API not started")
