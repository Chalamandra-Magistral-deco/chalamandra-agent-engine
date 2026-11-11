#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CATEDRALX_ROOT="$(cd "$ROOT/../CatedralX" && pwd)"

usage() { echo "Uso: deploy/build-candidate-images.sh [--plan]"; }
if [[ $# -gt 1 ]] || [[ $# -eq 1 && "$1" != "--plan" ]]; then
  usage >&2
  exit 2
fi
PLAN=false
if [[ $# -eq 1 ]]; then PLAN=true; fi

command -v docker >/dev/null || { echo "BLOCKED: docker no está instalado" >&2; exit 2; }
command -v python3 >/dev/null || { echo "BLOCKED: python3 no está instalado" >&2; exit 2; }
git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null
git -C "$CATEDRALX_ROOT" rev-parse --is-inside-work-tree >/dev/null

git_dirty() {
  if [[ -n "$(git -C "$1" status --porcelain --untracked-files=all)" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

tree_sha256() {
  python3 - "$1" <<'PYTREE'
import hashlib
import os
import subprocess
import sys

root = os.path.realpath(sys.argv[1])
raw = subprocess.check_output(
    ["git", "-C", root, "ls-files", "-co", "--exclude-standard", "-z"]
)
paths = sorted(path for path in raw.split(b"\0") if path)
digest = hashlib.sha256()
for relative in paths:
    absolute = os.path.join(root, os.fsdecode(relative))
    digest.update(b"PATH\0")
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    try:
        if os.path.islink(absolute):
            content = b"SYMLINK\0" + os.fsencode(os.readlink(absolute))
        else:
            with open(absolute, "rb") as stream:
                content = stream.read()
    except FileNotFoundError:
        content = b"DELETED\0"
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
print(digest.hexdigest())
PYTREE
}

AGENTS_REVISION="$(git -C "$ROOT" rev-parse HEAD)"
AGENTS_CODE_DIRTY="$(git_dirty "$ROOT")"
AGENTS_TREE_SHA256="$(tree_sha256 "$ROOT")"
CATEDRALX_REVISION="$(git -C "$CATEDRALX_ROOT" rev-parse HEAD)"
CATEDRALX_CODE_DIRTY="$(git_dirty "$CATEDRALX_ROOT")"
CATEDRALX_TREE_SHA256="$(tree_sha256 "$CATEDRALX_ROOT")"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
AGENTS_SHORT="$(printf '%s' "$AGENTS_REVISION" | cut -c1-7)"
CATEDRALX_SHORT="$(printf '%s' "$CATEDRALX_REVISION" | cut -c1-7)"
CATEDRALX_IMAGE="catedralx:validation-$CATEDRALX_SHORT-$STAMP"
API_IMAGE="agents-api:validation-$AGENTS_SHORT-cx-$CATEDRALX_SHORT-$STAMP"
WORKER_IMAGE="agents-worker:validation-$AGENTS_SHORT-cx-$CATEDRALX_SHORT-$STAMP"

printf 'Agents: %s (dirty=%s, tree=%s)\n' "$AGENTS_REVISION" "$AGENTS_CODE_DIRTY" "$AGENTS_TREE_SHA256"
printf 'CatedralX: %s (dirty=%s, tree=%s)\n' "$CATEDRALX_REVISION" "$CATEDRALX_CODE_DIRTY" "$CATEDRALX_TREE_SHA256"
printf 'New validation tags only:\n  %s\n  %s\n  %s\n' "$CATEDRALX_IMAGE" "$API_IMAGE" "$WORKER_IMAGE"

for image in "$CATEDRALX_IMAGE" "$API_IMAGE" "$WORKER_IMAGE"; do
  if sudo docker image inspect "$image" >/dev/null 2>&1; then
    echo "BLOCKED: el tag candidato ya existe; no se sobrescribirá: $image" >&2
    exit 3
  fi
done

if [[ "$PLAN" == "true" ]]; then
  echo "PLAN_ONLY: no se construyó ninguna imagen ni se tocó ningún contenedor."
  exit 0
fi

sudo docker build \
  --file "$CATEDRALX_ROOT/Dockerfile" \
  --build-arg "CATEDRALX_REVISION=$CATEDRALX_REVISION" \
  --build-arg "CATEDRALX_CODE_DIRTY=$CATEDRALX_CODE_DIRTY" \
  --label "com.chalamandra.catedralx.tree.sha256=$CATEDRALX_TREE_SHA256" \
  --label "org.opencontainers.image.revision=$CATEDRALX_REVISION" \
  --label "org.opencontainers.image.title=Chalamandra-CatedralX-Validation-Candidate" \
  --tag "$CATEDRALX_IMAGE" \
  "$CATEDRALX_ROOT"

actual_catedralx_revision="$(sudo docker image inspect "$CATEDRALX_IMAGE" --format '{{index .Config.Labels "com.chalamandra.catedralx.revision"}}')"
actual_catedralx_dirty="$(sudo docker image inspect "$CATEDRALX_IMAGE" --format '{{index .Config.Labels "com.chalamandra.catedralx.code.dirty"}}')"
[[ "$actual_catedralx_revision" == "$CATEDRALX_REVISION" && "$actual_catedralx_dirty" == "$CATEDRALX_CODE_DIRTY" ]] || {
  echo "BLOCKED: procedencia CatedralX no coincide con el checkout." >&2
  exit 4
}

sudo docker build \
  --file "$ROOT/deploy/Dockerfile" \
  --build-arg "CATEDRALX_BASE_IMAGE=$CATEDRALX_IMAGE" \
  --build-arg "AGENTS_REVISION=$AGENTS_REVISION" \
  --build-arg "AGENTS_CODE_DIRTY=$AGENTS_CODE_DIRTY" \
  --build-arg "CATEDRALX_REVISION=$CATEDRALX_REVISION" \
  --build-arg "CATEDRALX_CODE_DIRTY=$CATEDRALX_CODE_DIRTY" \
  --label "com.chalamandra.agents.worktree.sha256=$AGENTS_TREE_SHA256" \
  --label "com.chalamandra.catedralx.tree.sha256=$CATEDRALX_TREE_SHA256" \
  --label "org.opencontainers.image.revision=$AGENTS_REVISION" \
  --label "org.opencontainers.image.title=Chalamandra-Agents-Validation-Candidate" \
  --tag "$API_IMAGE" \
  "$ROOT"

sudo docker build \
  --file "$ROOT/deploy/Dockerfile.worker" \
  --build-arg "AGENTS_BASE_IMAGE=$API_IMAGE" \
  --label "com.chalamandra.agents.worktree.sha256=$AGENTS_TREE_SHA256" \
  --label "com.chalamandra.catedralx.tree.sha256=$CATEDRALX_TREE_SHA256" \
  --label "org.opencontainers.image.revision=$AGENTS_REVISION" \
  --label "org.opencontainers.image.title=Chalamandra-Agents-Worker-Validation-Candidate" \
  --tag "$WORKER_IMAGE" \
  "$ROOT"

for image in "$API_IMAGE" "$WORKER_IMAGE"; do
  actual_agents_revision="$(sudo docker image inspect "$image" --format '{{index .Config.Labels "com.chalamandra.agents.revision"}}')"
  actual_agents_dirty="$(sudo docker image inspect "$image" --format '{{index .Config.Labels "com.chalamandra.agents.code.dirty"}}')"
  actual_catedralx_revision="$(sudo docker image inspect "$image" --format '{{index .Config.Labels "com.chalamandra.catedralx.revision"}}')"
  actual_catedralx_dirty="$(sudo docker image inspect "$image" --format '{{index .Config.Labels "com.chalamandra.catedralx.code.dirty"}}')"
  actual_agents_tree="$(sudo docker image inspect "$image" --format '{{index .Config.Labels "com.chalamandra.agents.worktree.sha256"}}')"
  actual_catedralx_tree="$(sudo docker image inspect "$image" --format '{{index .Config.Labels "com.chalamandra.catedralx.tree.sha256"}}')"

  [[ "$actual_agents_revision" == "$AGENTS_REVISION" && "$actual_agents_dirty" == "$AGENTS_CODE_DIRTY" ]] || {
    echo "BLOCKED: procedencia Agents incorrecta en $image" >&2; exit 4;
  }
  [[ "$actual_catedralx_revision" == "$CATEDRALX_REVISION" && "$actual_catedralx_dirty" == "$CATEDRALX_CODE_DIRTY" ]] || {
    echo "BLOCKED: procedencia CatedralX incorrecta en $image" >&2; exit 4;
  }
  [[ "$actual_agents_tree" == "$AGENTS_TREE_SHA256" && "$actual_catedralx_tree" == "$CATEDRALX_TREE_SHA256" ]] || {
    echo "BLOCKED: huella de árbol incorrecta en $image" >&2; exit 4;
  }
done

echo "Candidate images built and provenance verified."
echo "No active image tag was reused; no container was started, stopped, or replaced."
