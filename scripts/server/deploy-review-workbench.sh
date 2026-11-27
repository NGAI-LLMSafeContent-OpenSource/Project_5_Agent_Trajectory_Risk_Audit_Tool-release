#!/usr/bin/env bash
# Deploy the Review Workbench the same way as Project 5 governance web:
# systemd runs the API on localhost, nginx serves the built UI and proxies /api.
# Run this on the 176 server. It does not use Docker.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../../workbench/api/app.py" ]]; then
  PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
else
  PROJECT_ROOT="${WORKBENCH_REPO:-/data/zhangxu/repos/Project_5_Agent_Trajectory_Risk_Audit_Tool}"
fi

PUBLIC_HOST="${WORKBENCH_PUBLIC_HOST:-platform.example.invalid}"
PORT="${WORKBENCH_PORT:-5861}"
BACKEND_PORT="${WORKBENCH_BACKEND_PORT:-8765}"
WWW_ROOT="${WORKBENCH_WWW_ROOT:-/data/zhangxu/www/review-workbench}"
NGINX_SITE_NAME="${WORKBENCH_NGINX_SITE_NAME:-llmsafe-review-workbench}"
SERVICE_NAME="${WORKBENCH_SERVICE_NAME:-llmsafe-review-workbench}"
DOCKER_NAME="${WORKBENCH_DOCKER_NAME:-review-workbench}"
STORAGE_ROOT="${WORKBENCH_STORAGE_ROOT:-/data/zhangxu/review-workbench}"
TOPIC4_FINIAL_ROOT="${TOPIC4_FINIAL_ROOT:-/data/zhangxu/repos/Project_4-5_ContentToolRisk/课题五/finial}"
TOPIC4_CONFIG="${TOPIC4_CONFIG:-${TOPIC4_FINIAL_ROOT}/config/toolchain.json}"
TOPIC4_ACTIVATE="${TOPIC4_ACTIVATE:-${TOPIC4_FINIAL_ROOT}/deployment/176/activate.sh}"
TOPIC4_PROFILE="${TOPIC4_PROFILE:-finial-1.0}"
TOPIC4_TIMEOUT="${TOPIC4_TIMEOUT:-21600}"
TOPIC4_CUDA_VISIBLE_DEVICES="${TOPIC4_CUDA_VISIBLE_DEVICES:-}"
PROXY_TIMEOUT="${WORKBENCH_PROXY_TIMEOUT:-21600s}"
TOPIC4_COMMAND="${PROJECT_ROOT}/scripts/server/run-topic4-cli.sh"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
DO_PULL=true
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: bash scripts/server/deploy-review-workbench.sh [options]

Update the Review Workbench on this machine without Docker.

The shape matches Project_5_LLM_Content_Safety_Governance_Web:
  systemd  -> API on 127.0.0.1:8765
  nginx    -> public port serves workbench/web/dist and proxies /api
  ufw      -> allow the public port when the firewall is active

Run it on the 176 server, from the repository, after the commit is on origin/main.
sudo is required for systemd, nginx, and ufw. The old static prototype on 5855
is left alone.

Options:
  --dry-run           Print the mutating commands and do not change the host.
  --no-pull           Do not git pull --ff-only.
  --host=HOST         nginx server_name (default: platform.example.invalid).
  --port=PORT         public nginx port (default: 5861).
  --backend-port=PORT API port (default: 8765).
  --www-root=PATH     static files (default: /data/zhangxu/www/review-workbench).
  -h, --help          Show this help.

Environment equivalents:
  WORKBENCH_REPO, WORKBENCH_PUBLIC_HOST, WORKBENCH_PORT,
  WORKBENCH_BACKEND_PORT, WORKBENCH_WWW_ROOT, WORKBENCH_NGINX_SITE_NAME,
  WORKBENCH_SERVICE_NAME, WORKBENCH_STORAGE_ROOT, TOPIC4_FINIAL_ROOT,
  TOPIC4_CONFIG, TOPIC4_ACTIVATE, TOPIC4_PROFILE, TOPIC4_TIMEOUT,
  TOPIC4_CUDA_VISIBLE_DEVICES,
  WORKBENCH_PROXY_TIMEOUT
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[review-workbench] %s\n' "$*"
}

quote_command() {
  printf '%q ' "$@"
  printf '\n'
}

run() {
  if [[ "${DRY_RUN}" == true ]]; then
    printf '[dry-run] '
    quote_command "$@"
    return 0
  fi
  "$@"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

validate_unit_value() {
  local name="$1"
  local value="$2"
  if [[ "${value}" == *$'\n'* || "${value}" == *$'\r'* || "${value}" == *'"'* \
    || "${value}" == *'\'* || "${value}" == *'%'* ]]; then
    die "${name} contains characters that cannot be written safely to the systemd unit"
  fi
}

while (($# > 0)); do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --no-pull) DO_PULL=false ;;
    --host=*) PUBLIC_HOST="${1#*=}" ;;
    --port=*) PORT="${1#*=}" ;;
    --backend-port=*) BACKEND_PORT="${1#*=}" ;;
    --www-root=*) WWW_ROOT="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

[[ "${PORT}" =~ ^[0-9]+$ ]] || die "invalid port: ${PORT}"
[[ "${BACKEND_PORT}" =~ ^[0-9]+$ ]] || die "invalid backend port: ${BACKEND_PORT}"
[[ "${PORT}" != "${BACKEND_PORT}" ]] || die "public port and backend port must differ"
[[ "${WWW_ROOT}" == /* ]] || die "www root must be absolute: ${WWW_ROOT}"
[[ "${STORAGE_ROOT}" == /* ]] || die "storage root must be absolute: ${STORAGE_ROOT}"
[[ "${TOPIC4_FINIAL_ROOT}" == /* ]] || die "Topic 4 root must be absolute: ${TOPIC4_FINIAL_ROOT}"
[[ "${TOPIC4_CONFIG}" == /* ]] || die "Topic 4 config must be absolute: ${TOPIC4_CONFIG}"
[[ "${TOPIC4_ACTIVATE}" == /* ]] || die "Topic 4 activate script must be absolute: ${TOPIC4_ACTIVATE}"
[[ "${TOPIC4_TIMEOUT}" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "invalid Topic 4 timeout: ${TOPIC4_TIMEOUT}"
[[ "${PROXY_TIMEOUT}" =~ ^[0-9]+s$ ]] || die "invalid nginx proxy timeout: ${PROXY_TIMEOUT}"
[[ "${TOPIC4_PROFILE}" == "finial-1.0" ]] || die "deployment requires Topic 4 profile finial-1.0"
[[ "${NGINX_SITE_NAME}" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid nginx site name: ${NGINX_SITE_NAME}"
[[ "${SERVICE_NAME}" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid service name: ${SERVICE_NAME}"
[[ -f "${PROJECT_ROOT}/workbench/api/app.py" ]] || die "workbench API not found under ${PROJECT_ROOT}"
[[ -f "${PROJECT_ROOT}/workbench/web/package.json" ]] || die "workbench web package not found under ${PROJECT_ROOT}"
[[ -x "${TOPIC4_COMMAND}" ]] || die "Topic 4 wrapper not executable: ${TOPIC4_COMMAND}"
[[ -f "${TOPIC4_CONFIG}" ]] || die "Topic 4 config not found: ${TOPIC4_CONFIG}"
[[ -f "${TOPIC4_ACTIVATE}" ]] || die "Topic 4 activation script not found: ${TOPIC4_ACTIVATE}"
for unit_setting in \
  "PROJECT_ROOT=${PROJECT_ROOT}" \
  "STORAGE_ROOT=${STORAGE_ROOT}" \
  "TOPIC4_FINIAL_ROOT=${TOPIC4_FINIAL_ROOT}" \
  "TOPIC4_ACTIVATE=${TOPIC4_ACTIVATE}" \
  "TOPIC4_COMMAND=${TOPIC4_COMMAND}" \
  "TOPIC4_CONFIG=${TOPIC4_CONFIG}" \
  "TOPIC4_PROFILE=${TOPIC4_PROFILE}" \
  "TOPIC4_TIMEOUT=${TOPIC4_TIMEOUT}" \
  "TOPIC4_CUDA_VISIBLE_DEVICES=${TOPIC4_CUDA_VISIBLE_DEVICES}"; do
  validate_unit_value "${unit_setting%%=*}" "${unit_setting#*=}"
done

require_cmd git
require_cmd uv
require_cmd npm
require_cmd rsync
require_cmd curl
if [[ "${DRY_RUN}" == false ]]; then
  require_cmd sudo
  require_cmd systemctl
  require_cmd nginx
fi

nginx_available="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
nginx_enabled="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
python_bin="${PROJECT_ROOT}/.venv/bin/python"

render_unit() {
  cat <<UNIT
[Unit]
Description=LLMSafe Review Workbench API
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${PROJECT_ROOT}
Environment="PYTHONPATH=${PROJECT_ROOT}"
Environment="WORKBENCH_STORAGE_ROOT=${STORAGE_ROOT}"
Environment="WORKBENCH_ALLOW_PROVIDER_OVERRIDES=false"
Environment="TOPIC4_FINIAL_ROOT=${TOPIC4_FINIAL_ROOT}"
Environment="TOPIC4_ACTIVATE=${TOPIC4_ACTIVATE}"
Environment="TOPIC4_COMMAND=${TOPIC4_COMMAND}"
Environment="TOPIC4_CONFIG=${TOPIC4_CONFIG}"
Environment="TOPIC4_PROFILE=${TOPIC4_PROFILE}"
Environment="TOPIC4_TIMEOUT=${TOPIC4_TIMEOUT}"
Environment="CUDA_VISIBLE_DEVICES=${TOPIC4_CUDA_VISIBLE_DEVICES}"
ExecStart="${python_bin}" -m uvicorn workbench.api.app:app --host 127.0.0.1 --port ${BACKEND_PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}

render_nginx() {
  cat <<NGINX
server {
    listen ${PORT};
    server_name ${PUBLIC_HOST};

    root ${WWW_ROOT};
    index index.html;
    client_max_body_size 128m;

    location /api/ {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout ${PROXY_TIMEOUT};
        proxy_read_timeout ${PROXY_TIMEOUT};
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
NGINX
}

docker_exists() {
  command -v docker >/dev/null 2>&1 || return 1
  docker ps -a --format '{{.Names}}' | grep -qx "${DOCKER_NAME}"
}

log "repo=${PROJECT_ROOT}"
log "public=http://${PUBLIC_HOST}:${PORT}/"
log "api=http://127.0.0.1:${BACKEND_PORT}/api/health"
log "www=${WWW_ROOT}"
log "storage=${STORAGE_ROOT}"
log "topic4=${TOPIC4_FINIAL_ROOT} (${TOPIC4_PROFILE})"
log "service=${SERVICE_NAME}"

if [[ "${DRY_RUN}" == true ]]; then
  [[ "${DO_PULL}" == true ]] && run git -C "${PROJECT_ROOT}" pull --ff-only
  run uv sync --group task-test --directory "${PROJECT_ROOT}"
  run bash -c "cd ${PROJECT_ROOT}/workbench/web && npm ci && npm run build"
  run mkdir -p "${WWW_ROOT}"
  run mkdir -p "${STORAGE_ROOT}"
  run rsync -a --delete "${PROJECT_ROOT}/workbench/web/dist/" "${WWW_ROOT}/"
  log "[dry-run] write ${unit_path}"
  render_unit
  log "[dry-run] write ${nginx_available}"
  render_nginx
  run sudo systemctl daemon-reload
  run sudo systemctl enable "${SERVICE_NAME}"
  run sudo systemctl restart "${SERVICE_NAME}"
  log "[dry-run] curl API health before touching nginx"
  if docker_exists; then
    run docker stop "${DOCKER_NAME}"
  fi
  run sudo nginx -t
  run sudo systemctl reload nginx
  log "[dry-run] curl public health"
  if docker_exists; then
    run docker rm "${DOCKER_NAME}"
  fi
  run sudo ufw allow "${PORT}/tcp" comment "LLMSafe review workbench"
  log "dry-run complete; no files or services were changed"
  exit 0
fi

if ! sudo -v; then
  die "sudo is required for systemd, nginx, and ufw"
fi

if [[ "${DO_PULL}" == true ]]; then
  log "updating repository"
  git -C "${PROJECT_ROOT}" pull --ff-only
fi

log "installing Python dependencies"
(
  cd "${PROJECT_ROOT}"
  uv sync --group task-test
)
[[ -x "${python_bin}" ]] || die "Python not found: ${python_bin}"

log "building frontend"
(
  cd "${PROJECT_ROOT}/workbench/web"
  npm ci
  npm run build
)
[[ -f "${PROJECT_ROOT}/workbench/web/dist/index.html" ]] || die "frontend build did not produce index.html"

log "publishing frontend"
mkdir -p "${WWW_ROOT}"
mkdir -p "${STORAGE_ROOT}"
rsync -a --delete "${PROJECT_ROOT}/workbench/web/dist/" "${WWW_ROOT}/"
find "${WWW_ROOT}" -type d -exec chmod 755 '{}' +
find "${WWW_ROOT}" -type f -exec chmod 644 '{}' +

log "installing systemd unit"
unit_tmp="$(mktemp)"
nginx_tmp="$(mktemp)"
cleanup() {
  rm -f "${unit_tmp}" "${nginx_tmp}"
}
trap cleanup EXIT
render_unit >"${unit_tmp}"
render_nginx >"${nginx_tmp}"
sudo install -m 0644 "${unit_tmp}" "${unit_path}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

log "checking API"
if ! curl -fsS --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 \
  "http://127.0.0.1:${BACKEND_PORT}/api/health" | grep -q '"ok":true'; then
  sudo systemctl status "${SERVICE_NAME}" --no-pager || true
  die "API health check failed; nginx was not changed. See: journalctl -u ${SERVICE_NAME} -n 80"
fi

had_docker=false
if docker_exists; then
  had_docker=true
  log "stopping Docker container ${DOCKER_NAME} so nginx can bind ${PORT}"
  docker stop "${DOCKER_NAME}"
fi

restore_docker() {
  if [[ "${had_docker}" == true ]]; then
    log "starting Docker container ${DOCKER_NAME} again"
    docker start "${DOCKER_NAME}" || true
  fi
}

log "installing nginx site"
sudo install -m 0644 "${nginx_tmp}" "${nginx_available}"
sudo ln -sfn "${nginx_available}" "${nginx_enabled}"
if ! sudo nginx -t; then
  sudo rm -f "${nginx_enabled}"
  sudo nginx -t && sudo systemctl reload nginx || true
  restore_docker
  die "nginx configuration test failed"
fi
if ! sudo systemctl reload nginx; then
  sudo rm -f "${nginx_enabled}"
  sudo nginx -t && sudo systemctl reload nginx || true
  restore_docker
  die "nginx reload failed"
fi

log "checking public site"
health_tmp="$(mktemp)"
page_tmp="$(mktemp)"
trap 'rm -f "${unit_tmp}" "${nginx_tmp}" "${health_tmp}" "${page_tmp}"' EXIT
if ! curl -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 10 \
  --output "${health_tmp}" "http://127.0.0.1:${PORT}/api/health" \
  || ! grep -q '"ok":true' "${health_tmp}" \
  || ! curl -fsS --max-time 10 --output "${page_tmp}" "http://127.0.0.1:${PORT}/" \
  || ! grep -Fq '<title>Agent 轨迹风险可视化审计</title>' "${page_tmp}"; then
  sudo rm -f "${nginx_enabled}"
  sudo nginx -t && sudo systemctl reload nginx || true
  restore_docker
  die "public health check failed; Docker container was restored if it had been stopped"
fi

if [[ "${had_docker}" == true ]]; then
  log "removing Docker container ${DOCKER_NAME}"
  docker rm "${DOCKER_NAME}"
fi

if sudo ufw status | grep -Fq 'Status: active'; then
  if sudo ufw status | grep -Eq "(^|[[:space:]])${PORT}/tcp[[:space:]]+ALLOW"; then
    log "ufw already allows ${PORT}/tcp"
  else
    log "allowing TCP ${PORT} through ufw"
    sudo ufw allow "${PORT}/tcp" comment "LLMSafe review workbench"
  fi
else
  log "ufw is inactive; no firewall rule was needed"
fi

log "deployment complete"
log "open http://${PUBLIC_HOST}:${PORT}/"
log "logs: journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
