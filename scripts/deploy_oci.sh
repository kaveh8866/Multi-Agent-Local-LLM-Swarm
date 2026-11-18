#!/usr/bin/env bash
# =============================================================================
# Remote bootstrap for the swarm host (run as ubuntu on the OCI instance)
# =============================================================================
# Expects to be executed from the repository root on the instance, after the
# operator has copied the source archive and a .env produced from .env.example.
# It verifies the block-volume mount, refuses to continue if the data volume is
# not mounted (weights would silently land on the boot disk), installs Docker if
# cloud-init did not, then brings the stack up and pulls the model.
#
#   ssh ubuntu@<ip> 'bash llm-swarm/scripts/deploy_oci.sh'
#
# No secret is printed or written by this script: .env must already exist.

set -euo pipefail

MOUNT_POINT="${SWARM_MOUNT_POINT:-/mnt/llm-swarm}"
MODEL_DIR="${SWARM_HOST_MODEL_DIR:-$MOUNT_POINT/models}"
STATE_DIR="${SWARM_HOST_STATE_DIR:-$MOUNT_POINT/state}"
DOCKER=(docker)
COMPOSE=("${DOCKER[@]}" compose --env-file .env -f docker/docker-compose.yml -f docker/compose.oci.yaml)

log() { printf 'deploy: %s\n' "$*"; }
fail() { printf 'deploy: ERROR %s\n' "$*" >&2; exit 1; }

[ -f .env ] || fail ".env is missing; create it from .env.example before deploying"
# A .env authored on Windows can arrive with CRLF; a trailing \r becomes part of
# the value ("invalid model name") and is invisible in a terminal.
if grep -q $'\r' .env; then
  sed -i 's/\r$//' .env
  log "normalised CRLF line endings in .env"
fi
[ -f docker/compose.oci.yaml ] || fail "docker/compose.oci.yaml is missing"

# --- 1. persistent storage ---------------------------------------------------
if ! findmnt --target "$MOUNT_POINT" >/dev/null; then
  # OCI can finish attaching the volume after cloud-init's first pass; the
  # systemd unit is designed to be re-run.
  log "$MOUNT_POINT not mounted yet; re-running llm-swarm-mount"
  sudo systemctl restart llm-swarm-mount || true
  sleep 5
fi
findmnt --target "$MOUNT_POINT" >/dev/null || fail "$MOUNT_POINT is not mounted (block volume unattached?)"
MOUNT_SOURCE="$(findmnt -no SOURCE --target "$MOUNT_POINT")"
MOUNT_FSTYPE="$(findmnt -no FSTYPE --target "$MOUNT_POINT")"
log "data volume $MOUNT_SOURCE ($MOUNT_FSTYPE) mounted at $MOUNT_POINT"

mkdir -p "$MODEL_DIR" "$STATE_DIR"
if [ "$(id -u)" -eq 0 ]; then
  chown -R 10001:10001 "$STATE_DIR"
fi

# --- 2. docker ---------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "docker missing; installing from the distribution packages"
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker.io docker-compose-v2
  sudo usermod -aG docker "$USER" || true
fi
if ! docker info >/dev/null 2>&1; then
  # Freshly installed Docker puts the user in the docker group, but that only
  # applies to a new login session. Fall back to sudo for this run.
  if sudo -n docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
    COMPOSE=("${DOCKER[@]}" compose --env-file .env -f docker/docker-compose.yml -f docker/compose.oci.yaml)
    log "using sudo for docker (group membership applies after re-login)"
  else
    fail "cannot reach the Docker daemon (try: sudo usermod -aG docker \$USER && newgrp docker)"
  fi
fi

# --- 3. compose overlay resolves --------------------------------------------
"${COMPOSE[@]}" config -q
log "compose configuration valid (base + OCI overlay)"

# --- 4. build and start ------------------------------------------------------
"${COMPOSE[@]}" up -d --build
log "containers started"

# --- 5. wait for the orchestrator to report ready ---------------------------
for attempt in $(seq 1 60); do
  if curl -fsS -m 5 http://127.0.0.1:8090/ready >/dev/null 2>&1; then
    log "orchestrator ready after $((attempt * 3))s"
    break
  fi
  [ "$attempt" -eq 60 ] && fail "/ready did not answer within 180s"
  sleep 3
done

# --- 6. model weights --------------------------------------------------------
MODEL="$(grep -E '^SWARM_LLM_MODEL=' .env | cut -d= -f2-)"
log "pulling $MODEL into $MODEL_DIR"
"${COMPOSE[@]}" exec -T llm-runtime ollama pull "$MODEL"
"${COMPOSE[@]}" exec -T llm-runtime ollama list

log "done: $(curl -fsS http://127.0.0.1:8090/ready)"
