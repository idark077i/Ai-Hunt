#!/usr/bin/env bash
# fetch_claude_red.sh — obtain the pristine Claude-Red source at the pinned revision.
#
# The runtime consumes Claude-Red only through scripts/integrate_claude_red.py,
# which reads Claude-Red/Skills/<category>/<folder>/SKILL.md. That source tree is
# an upstream project; this script (re)creates it at the exact revision the
# committed catalog was generated from, so the integration is always
# reproducible even if the folder is ever lost.
#
# Usage:
#   bash scripts/fetch_claude_red.sh           # clone/pin to PINNED_REV
#   bash scripts/fetch_claude_red.sh --check    # report only, no network writes
#
# After a successful fetch run:
#   python3 scripts/integrate_claude_red.py
#   python3 scripts/skill_registry.py check

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${ROOT}/Claude-Red"
UPSTREAM="https://github.com/SnailSploit/Claude-Red.git"
PINNED_REV="a8de813edc9f66f60d44cfe42d47750e89d9930b"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

have_tree() {
  [[ -f "${DEST}/Skills" ]] || [[ -d "${DEST}/Skills" ]]
}

if have_tree; then
  current="$(git -C "${DEST}" rev-parse HEAD 2>/dev/null || echo unknown)"
  if [[ "${current}" == "${PINNED_REV}" ]]; then
    echo "[ok] Claude-Red already present at pinned revision ${PINNED_REV:0:12}"
    exit 0
  fi
  echo "[info] Claude-Red present at ${current:0:12}; expected ${PINNED_REV:0:12}"
  if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    exit 1
  fi
fi

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  echo "[miss] Claude-Red not present at ${DEST}"
  exit 1
fi

if [[ -d "${DEST}/.git" ]]; then
  echo "[info] fetching pinned revision into existing clone"
  git -C "${DEST}" fetch --quiet origin "${PINNED_REV}" || true
  git -C "${DEST}" checkout --quiet "${PINNED_REV}"
else
  echo "[info] cloning ${UPSTREAM} -> ${DEST}"
  git clone --quiet "${UPSTREAM}" "${DEST}"
  git -C "${DEST}" checkout --quiet "${PINNED_REV}"
fi

final="$(git -C "${DEST}" rev-parse HEAD)"
if [[ "${final}" != "${PINNED_REV}" ]]; then
  echo "[FAIL] could not pin Claude-Red to ${PINNED_REV} (at ${final})" >&2
  exit 1
fi

echo "[ok] Claude-Red pinned at ${PINNED_REV:0:12}"
echo "[next] python3 scripts/integrate_claude_red.py && python3 scripts/skill_registry.py check"
