#!/usr/bin/env bash
# Read-only: compare active container provenance and critical files to local checkouts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CATEDRALX_ROOT="$(cd "$ROOT/../CatedralX" && pwd)"
command -v docker >/dev/null || { echo "BLOCKED: docker no está instalado" >&2; exit 2; }

git_dirty() { [[ -n "$(git -C "$1" status --porcelain --untracked-files=all)" ]] && printf true || printf false; }
tree_sha256() {
  python3 - "$1" <<'PYTREE'
import hashlib, os, subprocess, sys
root=os.path.realpath(sys.argv[1])
raw=subprocess.check_output(["git","-C",root,"ls-files","-co","--exclude-standard","-z"])
paths=sorted(p for p in raw.split(b"\0") if p)
d=hashlib.sha256()
for p in paths:
    f=os.path.join(root,os.fsdecode(p)); d.update(b"PATH\0"); d.update(len(p).to_bytes(8,"big")); d.update(p)
    try: content=b"SYMLINK\0"+os.fsencode(os.readlink(f)) if os.path.islink(f) else open(f,"rb").read()
    except FileNotFoundError: content=b"DELETED\0"
    d.update(len(content).to_bytes(8,"big")); d.update(content)
print(d.hexdigest())
PYTREE
}
AGENTS_REV="$(git -C "$ROOT" rev-parse HEAD)"; AGENTS_DIRTY="$(git_dirty "$ROOT")"; AGENTS_TREE="$(tree_sha256 "$ROOT")"
CX_REV="$(git -C "$CATEDRALX_ROOT" rev-parse HEAD)"; CX_DIRTY="$(git_dirty "$CATEDRALX_ROOT")"; CX_TREE="$(tree_sha256 "$CATEDRALX_ROOT")"
AGENTS_FILES=(api/server.py api/worker_loop.py api/task_bus.py api/candidate_store.py api/acceptance_store.py api/webhook_dispatcher.py pipeline/catedralx_tool.py)
CX_FILES=(elite/REASON/orchestrator.py elite/REASON/procesador.py)
for container in agents-api agents-worker; do
  sudo docker inspect "$container" >/dev/null 2>&1 || { echo "BLOCKED: falta contenedor $container" >&2; exit 3; }
  image_id="$(sudo docker inspect --format '{{.Image}}' "$container")"
  get_label() { sudo docker image inspect "$image_id" --format "{{index .Config.Labels \"$1\"}}"; }
  checks=(
    "$(get_label com.chalamandra.agents.revision)|$AGENTS_REV"
    "$(get_label com.chalamandra.agents.code.dirty)|$AGENTS_DIRTY"
    "$(get_label com.chalamandra.catedralx.revision)|$CX_REV"
    "$(get_label com.chalamandra.catedralx.code.dirty)|$CX_DIRTY"
    "$(get_label com.chalamandra.agents.worktree.sha256)|$AGENTS_TREE"
    "$(get_label com.chalamandra.catedralx.tree.sha256)|$CX_TREE"
  )
  for check in "${checks[@]}"; do actual="${check%%|*}"; expected="${check#*|}"; [[ -n "$actual" && "$actual" != "unknown" && "$actual" != "<no value>" && "$actual" == "$expected" ]] || { echo "DRIFT: $container label actual=$actual expected=$expected" >&2; exit 4; }; done
  for rel in "${AGENTS_FILES[@]}"; do
    local_hash="$(sha256sum "$ROOT/$rel" | awk '{print $1}')"; image_hash="$(sudo docker exec "$container" sha256sum "/app/$rel" | awk '{print $1}')"
    [[ "$local_hash" == "$image_hash" ]] || { echo "DRIFT: $container /app/$rel" >&2; exit 5; }
  done
  for rel in "${CX_FILES[@]}"; do
    local_hash="$(sha256sum "$CATEDRALX_ROOT/$rel" | awk '{print $1}')"; image_hash="$(sudo docker exec "$container" sha256sum "/app/CatedralX/$rel" | awk '{print $1}')"
    [[ "$local_hash" == "$image_hash" ]] || { echo "DRIFT: $container /app/CatedralX/$rel" >&2; exit 5; }
  done
  printf 'MATCH: %s image=%s agents=%s catedralx=%s\n' "$container" "$image_id" "${AGENTS_REV:0:12}" "${CX_REV:0:12}"
done
echo "PASS: runtime provenance and critical file hashes match both checkouts."
