#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SOURCE_DIR="${PROJECT_ROOT}/prototypes/review-workbench-a-visual"
V2_SOURCE_DIR="${PROJECT_ROOT}/prototypes/review-workbench-v2"
PUBLIC_HOST="${PROTOTYPE_PUBLIC_HOST:-10.184.17.176}"
PORT="${PROTOTYPE_PORT:-5855}"
DEPLOY_ROOT="${PROTOTYPE_DEPLOY_ROOT:-/var/www/llmsafe-review-workbench-prototype}"
NGINX_SITE_NAME="${PROTOTYPE_NGINX_SITE_NAME:-llmsafe-review-workbench-prototype}"
DRY_RUN=false
OPEN_FIREWALL=false

usage() {
  cat <<'USAGE'
Usage: bash scripts/server/deploy-review-workbench-prototype.sh [options]

Deploy the throwaway Review Workbench UI prototype as an isolated nginx site.
Run this script on the 176 server after pulling the repository.

Options:
  --dry-run           Print the resolved configuration and mutating commands.
  --host=HOST         nginx server_name (default: 10.184.17.176).
  --port=PORT         nginx listen port (default: 5855).
  --root=PATH         Release root (default: /var/www/llmsafe-review-workbench-prototype).
  --site-name=NAME    nginx site filename.
  --open-firewall     If UFW is active, allow PORT/tcp after a healthy deploy.
  -h, --help          Show this help.

Environment equivalents:
  PROTOTYPE_PUBLIC_HOST, PROTOTYPE_PORT, PROTOTYPE_DEPLOY_ROOT,
  PROTOTYPE_NGINX_SITE_NAME
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[review-workbench-prototype] %s\n' "$*"
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

while (($# > 0)); do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      ;;
    --host=*)
      PUBLIC_HOST="${1#*=}"
      ;;
    --port=*)
      PORT="${1#*=}"
      ;;
    --root=*)
      DEPLOY_ROOT="${1#*=}"
      ;;
    --site-name=*)
      NGINX_SITE_NAME="${1#*=}"
      ;;
    --open-firewall)
      OPEN_FIREWALL=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1 (see --help)"
      ;;
  esac
  shift
done

[[ -f "${SOURCE_DIR}/index.html" ]] || die "prototype entry not found: ${SOURCE_DIR}/index.html"
[[ -f "${V2_SOURCE_DIR}/index.html" ]] || die "v2 prototype entry not found: ${V2_SOURCE_DIR}/index.html"
[[ "${PORT}" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || die "invalid port: ${PORT}"
[[ "${DEPLOY_ROOT}" == /* ]] || die "deploy root must be absolute: ${DEPLOY_ROOT}"
[[ "${NGINX_SITE_NAME}" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid nginx site name: ${NGINX_SITE_NAME}"
[[ "${PUBLIC_HOST}" != *://* && "${PUBLIC_HOST}" != */* ]] || die "host must not contain a scheme or path"

if [[ "${DRY_RUN}" == false ]]; then
  require_cmd sudo
  require_cmd nginx
  require_cmd curl
  require_cmd rsync
  require_cmd sha256sum
  if [[ "${OPEN_FIREWALL}" == true ]]; then
    require_cmd ufw
  fi
fi

revision="$(git -C "${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
release_id="$(date -u +%Y%m%dT%H%M%SZ)-${revision}"
release_dir="${DEPLOY_ROOT}/releases/${release_id}"
current_link="${DEPLOY_ROOT}/current"
candidate_link="${DEPLOY_ROOT}/.current-${release_id}"
nginx_available="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
nginx_enabled="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
health_url="http://127.0.0.1:${PORT}/?variant=tree"
v2_health_url="http://127.0.0.1:${PORT}/v2/?variant=v2"
v2_rsync_args=(-a --delete --exclude='*.py' --exclude='*.md' "${V2_SOURCE_DIR}/" "${release_dir}/v2/")

render_nginx_config() {
  cat <<NGINX
server {
    listen ${PORT};
    server_name ${PUBLIC_HOST};

    root ${current_link};
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "no-referrer" always;
}
NGINX
}

log "source=${SOURCE_DIR}/index.html"
log "source-v2=${V2_SOURCE_DIR}/"
log "release=${release_dir}"
log "public=http://${PUBLIC_HOST}:${PORT}/?variant=tree"
log "nginx-site=${nginx_available}"

if [[ "${DRY_RUN}" == true ]]; then
  run sudo mkdir -p "${release_dir}/v2" /etc/nginx/sites-available /etc/nginx/sites-enabled
  run sudo rsync -a --delete "${SOURCE_DIR}/index.html" "${release_dir}/index.html"
  run sudo rsync "${v2_rsync_args[@]}"
  log "[dry-run] write nginx site ${nginx_available}:"
  render_nginx_config
  run sudo ln -sfn "${nginx_available}" "${nginx_enabled}"
  run sudo nginx -t
  run sudo rm -f "${candidate_link}"
  run sudo ln -s "releases/${release_id}" "${candidate_link}"
  run sudo mv -Tf "${candidate_link}" "${current_link}"
  run sudo systemctl reload nginx
  printf '[dry-run] curl -fsS %q and verify prototype title\n' "${health_url}"
  printf '[dry-run] curl -fsS %q and verify v2 title\n' "${v2_health_url}"
  if [[ "${OPEN_FIREWALL}" == true ]]; then
    run sudo ufw allow "${PORT}/tcp" comment "LLMSafe review workbench prototype"
  fi
  log "dry-run complete; no files or services were changed"
  exit 0
fi

if [[ -e "${current_link}" && ! -L "${current_link}" ]]; then
  die "refusing to replace non-symlink path: ${current_link}"
fi

previous_target=""
if [[ -L "${current_link}" ]]; then
  previous_target="$(readlink "${current_link}")"
fi

nginx_tmp="$(mktemp)"
health_tmp="$(mktemp)"
nginx_backup_tmp="$(mktemp)"
cleanup() {
  rm -f "${nginx_tmp}" "${health_tmp}" "${nginx_backup_tmp}"
}
trap cleanup EXIT

render_nginx_config >"${nginx_tmp}"

had_nginx_site=false
if sudo test -f "${nginx_available}"; then
  sudo cat "${nginx_available}" >"${nginx_backup_tmp}"
  had_nginx_site=true
fi

rollback() {
  log "restoring the previous deployment"
  if [[ -n "${previous_target}" ]]; then
    sudo rm -f "${candidate_link}"
    sudo ln -s "${previous_target}" "${candidate_link}"
    sudo mv -Tf "${candidate_link}" "${current_link}"
  else
    sudo rm -f "${current_link}"
  fi

  if [[ "${had_nginx_site}" == true ]]; then
    sudo install -m 0644 "${nginx_backup_tmp}" "${nginx_available}"
    sudo ln -sfn "${nginx_available}" "${nginx_enabled}"
  else
    sudo rm -f "${nginx_enabled}" "${nginx_available}"
  fi

  if sudo nginx -t; then
    sudo systemctl reload nginx || true
  else
    log "warning: restored nginx configuration still fails nginx -t; manual recovery is required"
  fi
}

log "publishing immutable release"
run sudo mkdir -p "${release_dir}/v2" /etc/nginx/sites-available /etc/nginx/sites-enabled
run sudo rsync -a --delete "${SOURCE_DIR}/index.html" "${release_dir}/index.html"
run sudo rsync "${v2_rsync_args[@]}"
run sudo chown -R root:root "${release_dir}"
run sudo find "${release_dir}" -type d -exec chmod 0755 '{}' +
run sudo find "${release_dir}" -type f -exec chmod 0644 '{}' +
run sudo install -m 0644 "${nginx_tmp}" "${nginx_available}"
run sudo ln -sfn "${nginx_available}" "${nginx_enabled}"

log "validating nginx configuration before switching current"
if ! sudo nginx -t; then
  rollback
  die "nginx configuration validation failed; deployment was rolled back"
fi
run sudo rm -f "${candidate_link}"
run sudo ln -s "releases/${release_id}" "${candidate_link}"
run sudo mv -Tf "${candidate_link}" "${current_link}"
if ! sudo systemctl reload nginx; then
  rollback
  die "nginx reload failed; deployment was rolled back"
fi

log "checking ${health_url}"
if ! curl -fsS --retry 3 --retry-connrefused --max-time 10 \
  --output "${health_tmp}" "${health_url}" \
  || ! grep -Fq '<title>Agent 轨迹风险取证 · 结构原型</title>' "${health_tmp}"; then
  log "health check failed"
  rollback
  die "deployment rolled back because the prototype health check failed"
fi

log "checking ${v2_health_url}"
if ! curl -fsS --retry 3 --retry-connrefused --max-time 10 \
  --output "${health_tmp}" "${v2_health_url}" \
  || ! grep -Fq '<title>Agent 轨迹风险可视化审计</title>' "${health_tmp}"; then
  log "v2 health check failed"
  rollback
  die "deployment rolled back because the v2 prototype health check failed"
fi

published_sha256="$(sha256sum "${SOURCE_DIR}/index.html" | awk '{print $1}')"
log "health check passed; sha256=${published_sha256}"

if [[ "${OPEN_FIREWALL}" == true ]]; then
  if sudo ufw status | grep -Fq 'Status: active'; then
    log "allowing TCP ${PORT} through UFW"
    run sudo ufw allow "${PORT}/tcp" comment "LLMSafe review workbench prototype"
  else
    log "UFW is inactive; no firewall rule was needed"
  fi
fi

log "deployment complete"
log "Forensic Tree: http://${PUBLIC_HOST}:${PORT}/?variant=tree"
log "Risk Graph: http://${PUBLIC_HOST}:${PORT}/?variant=graph"
log "Evidence Chain: http://${PUBLIC_HOST}:${PORT}/?variant=chain"
log "Audit Workbench v2: http://${PUBLIC_HOST}:${PORT}/?variant=v2"
log "If the URL is unreachable from another machine, ask the server administrator to allow TCP ${PORT}."
