#!/bin/sh
# Docker/Compose only: image imports and isolated state, no host Python needed.
set -eu
umask 077
PTCG_PACKAGE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$PTCG_PACKAGE"

verify() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum --check SHA256SUMS >/dev/null
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 --check SHA256SUMS >/dev/null
    else
        echo "Install sha256sum or shasum first" >&2; exit 1
    fi
}

image_matches() {
    # Classic Docker reports the config digest; containerd reports the OCI manifest digest.
    [ "$1" = "$2 $PLATFORM" ] || [ "$1" = "$3 $PLATFORM" ]
}

inspect_image() {
    docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$1"
}

load_one() {
    if [ "${PTCG_FORCE_LOAD:-0}" != 1 ]; then
        actual=$(inspect_image "$2" 2>/dev/null) || actual=
        if image_matches "$actual" "$3" "$4"; then return 0; fi
    fi
    docker load --input "images/$1.tar.gz"
    actual=$(inspect_image "$2") || exit 1
    if ! image_matches "$actual" "$3" "$4"; then
        echo "Wrong image ID or architecture: $1 ($2)" >&2
        echo "Expected $PLATFORM with config $3 or OCI manifest $4; got $actual" >&2
        exit 1
    fi
}

load_images() {
    server=$(docker info --format '{{.OSType}}/{{.Architecture}}')
    case "$server" in
        linux/x86_64) server=linux/amd64 ;;
        linux/aarch64) server=linux/arm64 ;;
    esac
    if [ "$server" != "$PLATFORM" ] && [ "${PTCG_ALLOW_EMULATION:-0}" != 1 ]; then
        echo "Package requires $PLATFORM; Docker host is $server. Use the matching package." >&2; exit 1
    fi
    load_one api "$API_IMAGE" "$API_ID" "$API_MANIFEST_ID"
    load_one postgres "$POSTGRES_IMAGE" "$POSTGRES_ID" "$POSTGRES_MANIFEST_ID"
    load_one caddy "$CADDY_IMAGE" "$CADDY_ID" "$CADDY_MANIFEST_ID"
}

compose() {
    [ ! -L state ] && [ ! -L state/deployment ] && [ -f state/deployment/compose.yaml ] || {
        echo "Run ./server.sh setup --host <domain> --email <email> first" >&2; exit 1;
    }
    docker compose --project-directory "$PTCG_PACKAGE/state/deployment" \
        -f "$PTCG_PACKAGE/state/deployment/compose.yaml" "$@"
}

operation=${1:-help}
if [ "$#" -gt 0 ]; then shift; fi
case "$operation" in
    help|-h|--help)
        echo "Usage: ./server.sh verify|load|setup|start|check|check-public|status|logs|stop"
        echo "First: ./server.sh setup --host api.example.com --email operator@example.com"
        echo "Then:  ./server.sh start"
        exit 0 ;;
esac
case "$operation" in
    verify|load|setup|start|check-public)
        verify
        # Generated constants covered by checksums, never user shell input.
        . ./images.env ;;
esac
case "$operation" in
    verify) echo "Package checksums passed" ;;
    load)
        if [ "$#" -gt 0 ]; then
            [ "$#" -eq 1 ] && [ "$1" = --force ] || { echo "Use load [--force]" >&2; exit 1; }
            PTCG_FORCE_LOAD=1
        fi
        load_images ;;
    setup)
        load_images
        [ ! -L state ] || { echo "State must not be a symbolic link" >&2; exit 1; }
        if [ ! -d state ]; then mkdir -m 700 state; fi
        docker run --rm --pull=never --platform "$PLATFORM" --network none --read-only \
            --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges:true \
            -v "$PTCG_PACKAGE:/kit:ro" -v "$PTCG_PACKAGE/state:/state" \
            --entrypoint python "$API_IMAGE" -B /kit/configure.py "$@" ;;
    start)
        load_images
        compose config --quiet
        compose up --detach --wait --wait-timeout 150
        echo "API started. Run ./server.sh check and, after ACME issuance, check-public." ;;
    check)
        compose exec -T api python -c 'import os,urllib.request; r=urllib.request.Request("http://127.0.0.1:8784/readyz",headers={"Host":os.environ["PTCG_ONLINE_V2_PUBLIC_ORIGIN"].split("://",1)[1]}); urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=5); print("Internal API ready")' ;;
    check-public)
        load_images
        docker run --rm --pull=never --platform "$PLATFORM" --network host --read-only \
            --cap-drop ALL --security-opt no-new-privileges:true --entrypoint python "$API_IMAGE" \
            -c 'import json,sys,urllib.request; u=sys.argv[1]+"/readyz"; r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(u,timeout=10); assert r.status==200 and json.load(r).get("ready"); print("Public TLS chain and API readiness passed")' \
            "$(cat state/deployment/origin.txt)" ;;
    status) compose ps ;;
    logs) compose logs --tail 100 api caddy ;;
    stop) compose down ;;
    *) echo "Unknown command: $operation" >&2; exit 1 ;;
esac
