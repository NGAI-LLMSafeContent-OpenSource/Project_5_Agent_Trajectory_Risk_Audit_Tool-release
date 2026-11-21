#!/usr/bin/env bash
# Run the Topic 4 CLI with the environment installed on the 176 deployment host.
set -Eeuo pipefail

TOPIC4_FINIAL_ROOT="${TOPIC4_FINIAL_ROOT:-/data/zhangxu/repos/Project_4-5_ContentToolRisk/课题五/finial}"
TOPIC4_ACTIVATE="${TOPIC4_ACTIVATE:-${TOPIC4_FINIAL_ROOT}/deployment/176/activate.sh}"

[[ -f "${TOPIC4_ACTIVATE}" ]] || {
  printf 'Topic 4 activation script not found: %s\n' "${TOPIC4_ACTIVATE}" >&2
  exit 2
}
[[ -f "${TOPIC4_FINIAL_ROOT}/topic4-audit" ]] || {
  printf 'Topic 4 CLI not found: %s\n' "${TOPIC4_FINIAL_ROOT}/topic4-audit" >&2
  exit 2
}

# shellcheck disable=SC1090
source "${TOPIC4_ACTIVATE}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"
TOPIC4_PYTHON="${TOPIC4_PYTHON:-${TOPIC4_DEPLOY_ROOT}/env/bin/python}"
[[ -x "${TOPIC4_PYTHON}" ]] || {
  printf 'Topic 4 Python not executable: %s\n' "${TOPIC4_PYTHON}" >&2
  exit 2
}

exec "${TOPIC4_PYTHON}" "${TOPIC4_FINIAL_ROOT}/topic4-audit" "$@"
