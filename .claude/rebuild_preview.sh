#!/bin/zsh
# Rebuild the Lite app's /tmp preview sandbox after a macOS /tmp purge.
# The preview runner is TCC-sandboxed and cannot read ~/Documents, so
# everything (venv, backend, frontend, launcher) must live in /tmp.
#
# TCC quirks on this Mac (discovered 2026-08-08) force a two-phase build,
# because Claude Code's two shell modes have different file access:
#  - the SANDBOXED shell can read ~/Documents with plain data reads (cat,
#    grep) but cp/rsync/tar/ditto fail with EPERM (xattr reads are blocked),
#    and pip fails inside it (blocked from its own /var/folders temp dirs);
#  - the UNSANDBOXED shell cannot read ~/Documents at all, but pip works.
#
# Usage:
#   zsh .claude/rebuild_preview.sh stage   # sandboxed shell: mirror repo -> /tmp via cat
#   zsh .claude/rebuild_preview.sh venv    # unsandboxed shell: build venv + pip install
#   zsh .claude/rebuild_preview.sh patch   # either shell: apply patches, install launcher
# Then: preview_start name=boord-preview (port 8823).
# "venv" is only needed after a /tmp purge; "stage" + "patch" after every code change.
set -e
LITE="$(cd "$(dirname "$0")/.." && pwd)"
cd /tmp
STEP="${1:-all}"

# cat-based recursive copy: the only Documents read primitive TCC lets us use.
copytree() {
  local src="$1" dst="$2"
  rm -rf "$dst"
  (cd "$src" && find . -type f ! -path "./.venv/*" ! -name "*.pyc" ! -path "*/__pycache__/*" ! -name ".DS_Store") | \
  while IFS= read -r f; do
    mkdir -p "$dst/$(dirname "$f")"
    cat "$src/$f" > "$dst/$f"
  done
}

if [[ "$STEP" == "stage" || "$STEP" == "all" ]]; then
  echo "== staging mirrors + build inputs into /tmp =="
  copytree "$LITE/backend" /tmp/boord_lite_backend_copy
  copytree "$LITE/frontend" /tmp/boord_lite_frontend
  cat "$LITE/backend/requirements.txt" > /tmp/boord_lite_requirements.txt
  cat "$LITE/.claude/preview_patches.py" > /tmp/boord_lite_patches.py
  cat "$LITE/.claude/preview_launcher.py" > /tmp/boord_lite_launcher.py
fi

if [[ "$STEP" == "venv" || "$STEP" == "all" ]]; then
  if ! /tmp/boord_lite_venv/bin/python3 -c "import fastapi, uvicorn, sqlmodel, openpyxl, pandas, passlib, jose, multipart" 2>/dev/null; then
    echo "== creating venv =="
    rm -rf /tmp/boord_lite_venv
    /usr/bin/python3 -m venv /tmp/boord_lite_venv
    /tmp/boord_lite_venv/bin/pip install --quiet --upgrade pip
    /tmp/boord_lite_venv/bin/pip install --quiet -r /tmp/boord_lite_requirements.txt
  else
    echo "== venv already good =="
  fi
fi

if [[ "$STEP" == "patch" || "$STEP" == "all" ]]; then
  echo "== applying preview patches =="
  /tmp/boord_lite_venv/bin/python3 /tmp/boord_lite_patches.py
fi

echo "done ($STEP)"
