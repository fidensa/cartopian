#!/usr/bin/env bash
# Sourced before any agent/version/permission probe. No PATH discovery here.
_CARTOPIAN_ACCESS_PROJECT="${CARTOPIAN_PROJECT_ROOT:-}"
if [[ -z "$_CARTOPIAN_ACCESS_PROJECT" && "$2" == */prompts/* ]]; then
  _CARTOPIAN_ACCESS_PROJECT="${2%/prompts/*}"
fi
if [[ -z "$_CARTOPIAN_ACCESS_PROJECT" ]]; then
  _CARTOPIAN_ACCESS_PROJECT="${CARTOPIAN_LAUNCH_CWD:-}"
fi
if [[ -n "${CARTOPIAN_ROLE:-}${CARTOPIAN_PROJECT_ROOT:-}" || -f "$_CARTOPIAN_ACCESS_PROJECT/cartopian.toml" ]]; then
  if [[ "$_CARTOPIAN_ACCESS_PROJECT" != /* || ! -d "$_CARTOPIAN_ACCESS_PROJECT" ]]; then
    echo "cartopian-$1: project work access requires an absolute project binding" >&2
    exit 1
  fi
  if [[ "${CARTOPIAN_PYTHON:-}" != /* || ! -f "$CARTOPIAN_PYTHON" || ! -x "$CARTOPIAN_PYTHON" ]]; then
    echo "cartopian-$1: project work access requires dispatch-bound CARTOPIAN_PYTHON; launch through cartopian dispatch" >&2
    exit 1
  fi
  _CARTOPIAN_ACCESS_HELPER="${_CARTOPIAN_WRAPPER_DIR}/../../cli/work_access.py"
  if [[ ! -f "$_CARTOPIAN_ACCESS_HELPER" ]]; then
    echo "cartopian-$1: missing required work-access helper; reinstall Cartopian" >&2
    exit 1
  fi
  if ! "$CARTOPIAN_PYTHON" -I -S "$_CARTOPIAN_ACCESS_HELPER" --wrapper "$1" --project-dir "$_CARTOPIAN_ACCESS_PROJECT"; then
    exit 1
  fi
fi
