#!/bin/sh
# In-place update of one existing, single-file PTCG Lab Compose deployment.
set -eu
umask 077
package=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
fail() { printf '%s\n' "$*" >&2; exit 1; }
case "${1-}" in
    -h|--help)
        echo "Usage: ./update.sh [existing compose.yaml [existing Compose project name]]"
        exit 0 ;;
esac
[ "$#" -le 2 ] || fail "Use ./update.sh [compose.yaml [project]]."

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$package" && sha256sum -c SHA256SUMS)
else
    (cd "$package" && shasum -a 256 -c SHA256SUMS)
fi
# Generated, checksummed constants. The old deployment configuration is never sourced.
. "$package/update.env"
arch=$(docker info --format '{{.OSType}}/{{.Architecture}}')
case "$arch" in linux/x86_64) arch=linux/amd64 ;; linux/aarch64) arch=linux/arm64 ;; esac
[ "$arch" = "$PLATFORM" ] || [ "${PTCG_ALLOW_EMULATION:-0}" = 1 ] ||
    fail "This package requires $PLATFORM. Docker reports $arch."

base=${1-}
project=${2-}
if [ -z "$base" ]; then
    # Read only Compose labels. Never inspect or print container environment variables.
    seen=
    for id in $(docker ps --filter label=com.docker.compose.service=api --format '{{.ID}}'); do
        file=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$id")
        name=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")
        case "$file" in /*) ;; *) continue ;; esac
        case "$file" in *,*) continue ;; esac
        [ -f "$file" ] && grep -Eq "$OLD_RULE|$RULE" "$file" || continue
        match="$name:$file"
        if [ -n "$seen" ] && [ "$seen" != "$match" ]; then
            fail "Multiple PTCG deployments found. Pass the original compose.yaml and project."
        fi
        seen=$match
        base=$file
        project=$name
    done
fi
[ -n "$base" ] || fail "No running matching deployment found. Run ./update.sh /path/to/original/compose.yaml."
[ -f "$base" ] && [ ! -L "$base" ] || fail "The original Compose file must be a regular file."
directory=$(CDPATH= cd -- "$(dirname -- "$base")" && pwd -P)
base="$directory/$(basename -- "$base")"
# The fixed-config kit's manager verifies its entire original archive and Compose file.
# Its dedicated updater is a different deployment contract.
if [ -f "$directory/manage.py" ] && [ -f "$directory/MANIFEST.json" ]; then
    fail "This is a fixed-config manage.py kit. Keep it unchanged and use its matching update procedure."
fi
lock="$directory/.ptcg-api-update.lock"
mkdir "$lock" 2>/dev/null || fail "Another update is running or the lock requires inspection: $lock"
scratch=
stage=
mutating=0
completed=0
cleanup() {
    rc=$?
    trap - EXIT
    if [ "$mutating" = 1 ] && [ "$completed" = 0 ]; then
        dc "$base" stop api >/dev/null 2>&1 || true
        echo "Update interrupted. API was stopped where possible. Keep the backup and rerun the updater with the same Compose file." >&2
    fi
    if [ -n "$stage" ]; then rm -f "$stage"; fi
    if [ -n "$scratch" ]; then rm -rf "$scratch"; fi
    rmdir "$lock"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

dc() {
    config=$1
    shift
    if [ -n "$project" ]; then
        docker compose --project-directory "$directory" -p "$project" -f "$config" "$@"
    else
        docker compose --project-directory "$directory" -f "$config" "$@"
    fi
}
scratch=$(mktemp -d "$directory/.ptcg-update-work.XXXXXX")
stage="$scratch/compose.yaml"
# Compose resolves relative assets against the deployment directory. Save the
# resolved documents privately and only use them to compare effective settings.
dc "$base" config --format json > "$scratch/before.json"
docker load --input "$package/api-image.tar.gz"
actual=$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$IMAGE")
[ "$actual" = "$IMAGE_ID $PLATFORM" ] || [ "$actual" = "$IMAGE_MANIFEST_ID $PLATFORM" ] ||
    fail "The imported image identity does not match the package."

helper() {
    docker run --rm -i --pull=never --platform "$PLATFORM" --network none --read-only \
        --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges:true \
        -v "$scratch:/work:ro" --entrypoint python "$IMAGE" -B - "$@" < "$package/update-config.py"
}
cp -p "$base" "$scratch/original.yaml"
helper prepare "$IMAGE" "$PLATFORM" "$OLD_RULE" "$OLD_PIN" "$RULE" "$PIN" > "$stage"
dc "$stage" config --format json > "$scratch/after.json"
helper verify "$IMAGE" "$PLATFORM" "$OLD_RULE" "$OLD_PIN" "$RULE" "$PIN"

database=$(dc "$base" ps -q database)
[ -n "$database" ] || fail "The original database service must already be running."
database_config=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$database")
[ "$database_config" = "$base" ] ||
    fail "The running database was started from another Compose configuration."

kit=
kit_files="IMAGES.json MANIFEST.json compose.base.json images.env SHA256SUMS images/api.tar.gz"
journal="$directory/.ptcg-api-update-$UPDATE_ID.pending"
backup=
if [ "$(basename "$directory")" = deployment ] && [ "$(basename "$(dirname "$directory")")" = state ]; then
    candidate=$(dirname "$(dirname "$directory")")
    if [ -f "$candidate/IMAGES.json" ] && [ -f "$candidate/SHA256SUMS" ]; then
        kit=$candidate
    fi
fi
if [ -n "$kit" ]; then
    [ ! -L "$kit/images" ] && [ ! -L "$journal" ] && [ ! -L "$directory/DEPLOYMENT.json" ] ||
        fail "The offline kit has a linked image directory or update record."
    for name in $kit_files; do
        [ -f "$kit/$name" ] && [ ! -L "$kit/$name" ] ||
            fail "The offline kit is missing a regular file: $name"
    done
    cat "$package/UPDATE.json" > "$scratch/UPDATE.json"
    kit_source=$kit
    deployment_source="$directory/DEPLOYMENT.json"
    if [ -f "$journal" ]; then
        pending=$(cat "$journal")
        case "$pending" in
            .ptcg-api-backup-"$UPDATE_ID".*) ;;
            *) fail "The update recovery record is invalid." ;;
        esac
        [ "$pending" = "$(basename "$pending")" ] || fail "The update recovery path is invalid."
        backup="$directory/$pending"
        [ ! -L "$backup" ] && [ -f "$backup/COMPLETE" ] && [ -d "$backup/kit" ] ||
            fail "The offline kit update backup is missing."
        kit_source="$backup/kit"
        deployment_source="$kit_source/DEPLOYMENT.json"
    fi
    mkdir "$scratch/kit-input"
    for name in IMAGES.json MANIFEST.json compose.base.json SHA256SUMS; do
        cat "$kit_source/$name" > "$scratch/kit-input/$name"
    done
    if [ -f "$deployment_source" ]; then
        cat "$deployment_source" > "$scratch/kit-input/DEPLOYMENT.json"
    fi
    hash_file() {
        if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi
    }
    {
        for name in IMAGES.json compose.base.json images/api.tar.gz images.env; do
            value=$(hash_file "$kit_source/$name")
            printf '%s  %s\n' "${value%% *}" "$name"
        done
        for name in README.md configure.py images/caddy.tar.gz images/postgres.tar.gz public_api.py server.sh; do
            value=$(hash_file "$kit/$name")
            printf '%s  %s\n' "${value%% *}" "$name"
        done
    } > "$scratch/kit-input/actual-hashes"
    docker run --rm -i --pull=never --platform "$PLATFORM" --network none --read-only \
        --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges:true \
        -v "$scratch:/work" --entrypoint python "$IMAGE" -B - \
        kit "$IMAGE" "$PLATFORM" "$OLD_RULE" "$OLD_PIN" "$RULE" "$PIN" < "$package/update-config.py"
fi

replace_file() {
    tmp=$(mktemp "$(dirname "$2")/.ptcg-new.XXXXXX")
    if ! cp "$1" "$tmp" || ! mv -f "$tmp" "$2"; then
        rm -f "$tmp"
        fail "Unable to replace a required offline update file."
    fi
}

probe() {
    dc "$base" exec -T api python -c '
import json, os, ssl, sys, urllib.request
host = os.environ["PTCG_ONLINE_V2_PUBLIC_ORIGIN"].split("://", 1)[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for scheme in ("http", "https"):
    if scheme == "https":
        ca = "/run/tls/ca.pem"
        if not os.path.isfile(ca):
            continue
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
    def get(path):
        req = urllib.request.Request(scheme + "://127.0.0.1:8784" + path, headers={"Host": host})
        with opener.open(req, timeout=3) as response:
            return json.load(response)
    try:
        if not get("/readyz").get("ready"):
            continue
        capabilities = get("/api/online/v2/capabilities")
        if (capabilities["acceptingMatches"] and capabilities["ruleReleaseIds"] == [sys.argv[1]]
                and capabilities["resources"]["displayCatalogVersion"] == sys.argv[2]):
            sys.exit(0)
    except (OSError, ValueError, KeyError):
        continue
sys.exit(1)
' "$RULE" "$CATALOG" >/dev/null 2>&1
}

dc "$base" exec -T database sh -ec 'pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
if [ -n "$backup" ]; then
    mutating=1
    dc "$base" stop api
elif cmp -s "$scratch/original.yaml" "$stage" && [ ! -d "$scratch/kit-update" ]; then
    if probe; then
        echo "Already updated and ready: $RULE"
        exit 0
    fi
    found=
    for saved in "$directory"/.ptcg-api-backup-"$UPDATE_ID".*; do
        if [ -f "$saved/COMPLETE" ]; then found=$saved; fi
    done
    [ -n "$found" ] || fail "The target is already configured for this image but is not ready. No previous update backup was found."
    mutating=1
    dc "$base" stop api
else
    mutating=1
    dc "$base" stop api
    backup=$(mktemp -d "$directory/.ptcg-api-backup-$UPDATE_ID.XXXXXX")
    cp -p "$scratch/original.yaml" "$backup/compose.yaml"
    dc "$base" exec -T database sh -ec '
        export PGPASSWORD="${POSTGRES_PASSWORD:-}"
        exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc
    ' > "$backup/database.dump"
    [ -s "$backup/database.dump" ] || fail "Database backup is empty."
    dc "$base" exec -T database pg_restore --list < "$backup/database.dump" >/dev/null
    if [ -d "$scratch/kit-update" ]; then
        mkdir -p "$backup/kit/images"
        for name in $kit_files; do cp -p "$kit/$name" "$backup/kit/$name"; done
        if [ -f "$directory/DEPLOYMENT.json" ]; then
            cp -p "$directory/DEPLOYMENT.json" "$backup/kit/DEPLOYMENT.json"
        fi
    fi
    printf '%s\n' "$OLD_RULE" > "$backup/COMPLETE"
    if [ -d "$scratch/kit-update" ]; then
        printf '%s\n' "$(basename "$backup")" > "$scratch/pending"
        mv -f "$scratch/pending" "$journal"
    fi
    echo "Verified pre-update backup: $backup"
fi

cmp -s "$base" "$scratch/original.yaml" || fail "The original Compose file changed during the update."
if ! cmp -s "$base" "$stage"; then
    mv -f "$stage" "$base"
    stage=
fi
if [ -d "$scratch/kit-update" ]; then
    replace_file "$package/api-image.tar.gz" "$kit/images/api.tar.gz"
    for name in IMAGES.json MANIFEST.json compose.base.json images.env; do
        replace_file "$scratch/kit-update/$name" "$kit/$name"
    done
    if [ -f "$scratch/kit-update/DEPLOYMENT.json" ]; then
        replace_file "$scratch/kit-update/DEPLOYMENT.json" "$directory/DEPLOYMENT.json"
    fi
    # The original launcher can verify the whole new set after this last switch.
    replace_file "$scratch/kit-update/SHA256SUMS" "$kit/SHA256SUMS"
fi

dc "$base" run --rm --no-deps migrate
dc "$base" up -d --no-deps --no-build api
attempt=0
until probe; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 45 ] || fail "The API did not report the expected rule and catalog."
    sleep 2
done
if [ -n "$kit" ] && [ -f "$journal" ]; then rm -f "$journal"; fi
completed=1
echo "Update complete. The original Compose configuration now starts $RULE."
