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
  if ! _CARTOPIAN_ACCESS_BACKEND=$("$CARTOPIAN_PYTHON" -I -S "$_CARTOPIAN_ACCESS_HELPER" --wrapper "$1" --project-dir "$_CARTOPIAN_ACCESS_PROJECT" --emit-backend); then
    exit 1
  fi
fi

_CARTOPIAN_ACCESS_ADAPTER="$1"
_CARTOPIAN_ACCESS_PROMPT="$2"
# Mutates the wrapper's CMD array before its existing timeout is applied.
# The controller remains outside Seatbelt; the CLI and all its tools are inside.
cartopian_contain_command() {
  if [[ "${_CARTOPIAN_ACCESS_BACKEND:-}" == "seatbelt" ]]; then
    CMD=("$CARTOPIAN_PYTHON" -I -S "${_CARTOPIAN_WRAPPER_DIR}/../../cli/native_work_sandbox.py"
      --adapter "$_CARTOPIAN_ACCESS_ADAPTER" --project "$_CARTOPIAN_ACCESS_PROJECT"
      --prompt "$_CARTOPIAN_ACCESS_PROMPT" -- "${CMD[@]}")
  fi
}

# Devin's parser probes must have the same outer boundary as the final CLI.
cartopian_contained_probe() {
  if [[ "${_CARTOPIAN_ACCESS_BACKEND:-}" == "seatbelt" ]]; then
    "$CARTOPIAN_PYTHON" -I -S "${_CARTOPIAN_WRAPPER_DIR}/../../cli/native_work_sandbox.py" \
      --adapter "$_CARTOPIAN_ACCESS_ADAPTER" --project "$_CARTOPIAN_ACCESS_PROJECT" \
      --prompt "$_CARTOPIAN_ACCESS_PROMPT" --probe -- "$@"
  else
    "$@"
  fi
}
