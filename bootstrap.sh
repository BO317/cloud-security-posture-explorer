#!/bin/bash
# EC2 cloud-init user data for Ubuntu 24.04.
# Set AWS_REGION below to the region containing the SSM configuration parameter.
# Existing /etc/cloud-security-posture.env settings are preserved.
set -Eeuo pipefail
umask 022

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

touch /var/log/bootstrap.log
chmod 0600 /var/log/bootstrap.log
exec > >(tee -a /var/log/bootstrap.log) 2>&1
trap 'echo "[$(date -Is)] Bootstrap failed at line ${LINENO}."' ERR

exec 9>/run/cloud-security-posture-bootstrap.lock
flock 9

echo "[$(date -Is)] Starting bootstrap."

REPOSITORY="https://github.com/BO317/cloud-security-posture-explorer.git"
APP_DIR="/opt/cloud-security-posture-explorer"
SERVICE="cloud-security-posture-explorer"
ENV_FILE="/etc/cloud-security-posture.env"
AWS_REGION="${AWS_REGION:-us-east-1}"

export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=3 update
apt-get -o Acquire::Retries=3 install -y \
    ca-certificates git python3 python3-pip python3-venv

# SSM Agent may not have created this account/group yet.
if ! getent group ssm-user >/dev/null; then
    groupadd ssm-user
fi
if ! id ssm-user >/dev/null 2>&1; then
    useradd --create-home --gid ssm-user --shell /bin/bash ssm-user
fi

# Refuse redirected checkout roots before recursively changing permissions.
if [[ -L "$APP_DIR" || -L "${APP_DIR}/.git" ]]; then
    echo "Application directory or .git is a symlink; refusing ownership changes."
    exit 1
fi

repair_application_ownership() {
    # Include hidden .git and existing .venv files. Do not dereference venv
    # interpreter symlinks: their system targets must remain root-owned.
    chown -hR ssm-user:ssm-user -- "$APP_DIR"
    chmod -R u+rwX -- "$APP_DIR"
}

if [[ -d "${APP_DIR}/.git" ]]; then
    # Repair old root-owned deployments before running Git as the repository owner.
    repair_application_ownership
    if [[ "$(runuser -u ssm-user -- git -C "$APP_DIR" remote get-url origin)" != "$REPOSITORY" ]]; then
        echo "Existing checkout has an unexpected origin; refusing to overwrite it."
        exit 1
    fi
    echo "Preserving existing checkout at $(runuser -u ssm-user -- git -C "$APP_DIR" rev-parse HEAD)."
elif [[ -e "$APP_DIR" ]]; then
    echo "$APP_DIR already exists without a Git checkout; refusing to overwrite it."
    exit 1
else
    git clone --branch main --single-branch "$REPOSITORY" "$APP_DIR"
fi

repair_application_ownership

# Prefer the project's reproducible dependency lock.
REQUIREMENTS="${APP_DIR}/app/requirements-lock.txt"
if [[ ! -f "$REQUIREMENTS" ]]; then
    REQUIREMENTS="${APP_DIR}/app/requirements.txt"
fi
test -f "$REQUIREMENTS"

# Avoid modifying dependencies underneath a running service on repeat runs.
if systemctl is-active --quiet "${SERVICE}.service"; then
    systemctl stop "${SERVICE}.service"
fi

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
    runuser -u ssm-user -- python3 -m venv "${APP_DIR}/.venv"
fi
runuser -u ssm-user -- "${APP_DIR}/.venv/bin/python" -m pip install \
    --disable-pip-version-check -r "$REQUIREMENTS"
runuser -u ssm-user -- "${APP_DIR}/.venv/bin/python" -m pip check

# Ensure the complete checkout remains writable after dependency setup.
repair_application_ownership

if [[ ! -e "$ENV_FILE" ]]; then
    install -m 0600 /dev/null "$ENV_FILE"
    cat > "$ENV_FILE" <<EOF
# Bootstrap region for Parameter Store; no AWS credentials belong here.
AWS_REGION=${AWS_REGION}
# Optional local fallback scope. Empty scope produces UNKNOWN, never discovery.
ALLOWED_BUCKETS=
ALLOWED_SECURITY_GROUPS=
ALLOWED_INSTANCES=
AWS_METADATA_SERVICE_TIMEOUT=2
AWS_METADATA_SERVICE_NUM_ATTEMPTS=1
EOF
fi

cat > "/etc/systemd/system/${SERVICE}.service" <<'EOF'
[Unit]
Description=Cloud Security Posture Explorer
Wants=network-online.target
After=network.target network-online.target

[Service]
Type=simple
User=ssm-user
WorkingDirectory=/opt/cloud-security-posture-explorer
EnvironmentFile=/etc/cloud-security-posture.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=AWS_SHARED_CREDENTIALS_FILE=/dev/null
Environment=AWS_CONFIG_FILE=/dev/null
Environment=AWS_EC2_METADATA_DISABLED=false
UnsetEnvironment=AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_CONTAINER_CREDENTIALS_RELATIVE_URI AWS_CONTAINER_CREDENTIALS_FULL_URI AWS_EC2_METADATA_SERVICE_ENDPOINT
ExecStart=/opt/cloud-security-posture-explorer/.venv/bin/python -m app.server
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE}.service"
systemctl restart "${SERVICE}.service"

# Verify process liveness only; this does not establish a successful posture scan.
healthy=false
for attempt in {1..30}; do
    if "${APP_DIR}/.venv/bin/python" - <<'PY'
import json
import sys
from urllib.request import ProxyHandler, build_opener

try:
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:8000/healthz", timeout=2) as response:
        if response.status != 200 or json.load(response) != {"liveness": "ok"}:
            sys.exit(1)
except Exception:
    sys.exit(1)
PY
    then
        healthy=true
        break
    fi
    sleep 2
done

if [[ "$healthy" != true ]]; then
    echo "Service did not become live. Inspect: journalctl -u ${SERVICE}.service"
    exit 1
fi

echo "[$(date -Is)] Bootstrap complete; application liveness verified."
echo "Posture collection requires the EC2 role, SSM permission, and valid configuration."
echo "Dashboard: http://127.0.0.1:8000/ through authenticated SSH forwarding."
echo "Service logs: journalctl -u ${SERVICE}.service"
