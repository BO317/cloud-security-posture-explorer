#!/bin/bash
# Ubuntu 24.04 EC2 cloud-init user data. Run as root.
# The image must already exist in ECR; no application build happens on this host.
set -Eeuo pipefail
umask 022

IMAGE_TAG="${IMAGE_TAG:-1.1}"
ECR_REGION="us-east-1"
REGISTRY="980468996827.dkr.ecr.us-east-1.amazonaws.com"
CONTAINER_NAME="cloud-security-posture-explorer"
IMAGE="${REGISTRY}/${CONTAINER_NAME}:${IMAGE_TAG}"
ENV_FILE="/etc/cloud-security-posture.env"

[[ "$(id -u)" -eq 0 ]] || { echo 'Run this script as root.' >&2; exit 1; }
touch /var/log/bootstrap.log
chmod 0600 /var/log/bootstrap.log
exec > >(tee -a /var/log/bootstrap.log) 2>&1
log() { echo "[$(date -Is)] $*"; }
trap 'log "Bootstrap failed at line ${LINENO}."' ERR
exec 9>/run/cloud-security-posture-bootstrap.lock
flock 9

[[ "$IMAGE_TAG" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || {
    log 'Invalid IMAGE_TAG.'; exit 1;
}
log "Starting Docker deployment: ${IMAGE}"
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=3 update
# Ubuntu archive packages; no Git, venv, pip, or application dependencies.
apt-get -o Acquire::Retries=3 install -y ca-certificates docker.io curl jq sudo unzip
log 'Docker installed'
docker --version
systemctl enable docker
systemctl start docker
docker info >/dev/null
log 'Docker started'

# SSM Agent may not have created this account yet.
if ! getent group ssm-user >/dev/null; then groupadd ssm-user; fi
if ! id ssm-user >/dev/null 2>&1; then
    useradd --create-home --gid ssm-user --shell /bin/bash ssm-user
fi
# Append membership without replacing other groups. Existing sessions must reconnect.
# Docker socket access, like the sudo rule below, grants host administration rights.
usermod -aG docker ssm-user

WORK_DIR=$(mktemp -d)
chmod 0700 "$WORK_DIR"
trap 'rm -rf -- "$WORK_DIR"' EXIT

# Ubuntu 24.04 may have no awscli package candidate. Preserve an existing CLI;
# otherwise use AWS's official bundled v2 installer for this host architecture.
export PATH="/usr/local/bin:$PATH"
if command -v aws >/dev/null 2>&1; then
    log 'AWS CLI already installed; preserving existing installation'
else
    case "$(uname -m)" in
        x86_64) CLI_ARCH=x86_64 ;;
        aarch64|arm64) CLI_ARCH=aarch64 ;;
        *) log 'Unsupported architecture for AWS CLI v2'; exit 1 ;;
    esac
    log "Installing AWS CLI v2 (${CLI_ARCH})"
    curl --fail --show-error --silent --location --retry 3 \
        --connect-timeout 10 --max-time 300 \
        "https://awscli.amazonaws.com/awscli-exe-linux-${CLI_ARCH}.zip" \
        -o "$WORK_DIR/awscliv2.zip"
    unzip -q "$WORK_DIR/awscliv2.zip" -d "$WORK_DIR"
    # --update also tolerates a previous installation with a missing PATH link.
    bash "$WORK_DIR/aws/install" --bin-dir /usr/local/bin \
        --install-dir /usr/local/aws-cli --update
    hash -r
fi
# A failed download/installation/version check must stop before ECR deployment.
aws --version

# Session Manager accounts have no password; allow administrative log inspection.
# Validate before installing, and replace rather than append on repeat executions.
install -d -m 0755 /etc/sudoers.d
printf '%s\n' 'ssm-user ALL=(ALL) NOPASSWD:ALL' > "$WORK_DIR/sudoers"
visudo -cf "$WORK_DIR/sudoers"
install -o root -g root -m 0440 "$WORK_DIR/sudoers" /etc/sudoers.d/ssm-user

# Never source the optional environment file as shell code. Pass only application
# configuration, not credentials/profile/endpoint overrides, into the container.
# Accept KEY=value and simple paired quotes used by the old systemd env file.
ENV_ARGS=()
if [[ -e "$ENV_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        if [[ ! "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
            log 'Unsupported environment file syntax; use KEY=value.'; exit 1
        fi
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        case "$key" in
            SSM_REGION|AWS_REGION|AWS_DEFAULT_REGION|ALLOWED_BUCKETS|ALLOWED_SECURITY_GROUPS|ALLOWED_INSTANCE_NAME_TAGS|ALLOWED_INSTANCES|AWS_METADATA_SERVICE_TIMEOUT|AWS_METADATA_SERVICE_NUM_ATTEMPTS)
                if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
                    value="${value:1:${#value}-2}"
                fi
                ENV_ARGS+=(--env "$key=$value") ;;
            *) log 'Ignoring an unsupported environment setting (value not logged).' ;;
        esac
    done < "$ENV_FILE"
fi

# Force the AWS CLI to use the EC2 instance role, not inherited credentials.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN \
    AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE \
    AWS_CONTAINER_CREDENTIALS_RELATIVE_URI AWS_CONTAINER_CREDENTIALS_FULL_URI \
    AWS_EC2_METADATA_SERVICE_ENDPOINT AWS_ENDPOINT_URL AWS_ENDPOINT_URL_ECR
export AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_CONFIG_FILE=/dev/null
export AWS_EC2_METADATA_DISABLED=false AWS_EC2_METADATA_V1_DISABLED=true AWS_PAGER=""
# Store the short-lived ECR token only in a private temporary Docker config.
export DOCKER_CONFIG="$WORK_DIR/docker"
mkdir -m 0700 "$DOCKER_CONFIG"
aws ecr get-login-password --region "$ECR_REGION" |
    docker login --username AWS --password-stdin "$REGISTRY"
log 'ECR login successful'
docker pull "$IMAGE"
log 'Image pulled'

# Pull first: authentication/download failures must not stop the old application.
# Disable known legacy app units so they cannot reclaim port 8000 after a reboot.
# Leave their files and the old checkout intact for operator-managed rollback.
for unit in cloud-security-posture-explorer.service cloud-security-posture.service; do
    state=$(systemctl show "$unit" --property=LoadState --value)
    if [[ "$state" != not-found && -n "$state" ]]; then
        systemctl disable --now "$unit"
        log "Legacy application service disabled: $unit"
    fi
done
existing=$(docker container ls -a --filter "name=^/${CONTAINER_NAME}$" --format '{{.ID}}')
if [[ -n "$existing" ]]; then
    if [[ "$(docker inspect --format '{{.State.Running}}' "$existing")" == true ]]; then
        docker stop --time 20 "$existing"
    fi
    docker rm "$existing"
    log 'Container removed'
else
    log 'Container removed: none existed'
fi

# Bridge networking requires EC2 IMDSv2 response hop limit 2 for instance-role
# credentials. Configure that in the instance/launch template, not via this script.
# Keep port 8000 restricted by the security group; the app has no built-in login.
# Journald preserves host logs; CloudWatch delivery stays inside the application.
docker run -d --name "$CONTAINER_NAME" --restart unless-stopped \
    -p 8000:8000 --log-driver journald \
    "${ENV_ARGS[@]}" \
    -e HOST=0.0.0.0 -e PYTHONUNBUFFERED=1 \
    -e AWS_SHARED_CREDENTIALS_FILE=/dev/null -e AWS_CONFIG_FILE=/dev/null \
    -e AWS_EC2_METADATA_DISABLED=false -e AWS_EC2_METADATA_V1_DISABLED=true \
    "$IMAGE"
log 'Container started'

# Liveness only: a healthy server does not establish successful SSM/AWS scans.
# Parse JSON so whitespace is harmless; reject extra fields and multiple objects.
for attempt in {1..30}; do
    if [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" == true ]] &&
        body=$(curl --noproxy '*' --fail --silent --show-error \
            --connect-timeout 2 --max-time 3 http://127.0.0.1:8000/healthz) &&
        jq -e -s 'length == 1 and .[0] == {"liveness":"ok"}' <<< "$body" >/dev/null; then
        log 'Health check passed'
        log 'Bootstrap complete. Reconnect Session Manager to use the docker group.'
        exit 0
    fi
    sleep 2
done
log "Health check failed. Inspect: docker logs ${CONTAINER_NAME}"
exit 1
