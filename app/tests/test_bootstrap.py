"""Offline bootstrap workflow tests; host commands are faked, not executed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GIT_BASH = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/bin/bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")
FAKES = r'''
id() { if [[ "$1" == -u ]]; then echo 0; else test -f "$TEST_ROOT/user"; fi; }
getent() { test -f "$TEST_ROOT/group"; }
groupadd() { touch "$TEST_ROOT/group"; }
useradd() { touch "$TEST_ROOT/user"; }
apt-get() { echo "apt $*" >> "$TEST_ROOT/trace"; }
flock() { :; }
mkdir() { if [[ "$1" == -m ]]; then shift 2; fi; command mkdir "$@"; }
mktemp() { mkdir -p "$TEST_ROOT/work"; echo "$TEST_ROOT/work"; }
sleep() { :; }
command() {
    if [[ "$*" == '-v aws' && -f "$TEST_ROOT/missing-cli" && ! -f "$TEST_ROOT/cli-installed" ]]; then
        return 1
    fi
    builtin command "$@"
}
uname() { echo "${TEST_ARCH:-x86_64}"; }
unzip() {
    echo unzip >> "$TEST_ROOT/trace"
    [[ ! -f "$TEST_ROOT/fail-unzip" ]] || return 1
    mkdir -p "$WORK_DIR/aws"
    cat > "$WORK_DIR/aws/install" <<'INSTALLER'
#!/bin/bash
echo "cli-install $*" >> "$TEST_ROOT/trace"
[[ ! -f "$TEST_ROOT/fail-install" ]] || exit 1
touch "$TEST_ROOT/cli-installed"
INSTALLER
}
systemctl() {
    echo "systemctl $*" >> "$TEST_ROOT/trace"
    if [[ "$1" == start ]]; then touch "$TEST_ROOT/docker-started"; fi
    if [[ "$1" == show ]]; then
        if [[ -f "$TEST_ROOT/legacy" ]]; then echo loaded; else echo not-found; fi
    fi
}
usermod() {
    [[ "$*" == '-aG docker ssm-user' ]] || return 1
    test -f "$TEST_ROOT/user" && test -f "$TEST_ROOT/docker-started" || return 1
    echo docker-member >> "$TEST_ROOT/trace"
}
visudo() {
    [[ "$1" == -cf && "$(cat "$2")" == 'ssm-user ALL=(ALL) NOPASSWD:ALL' ]] || return 1
    [[ ! -f "$TEST_ROOT/reject-sudoers" ]]
}
install() {
    if [[ "$1" == -o ]]; then
        [[ "$2" == root && "$3" == -g && "$4" == root && "$5" == -m && "$6" == 0440 ]] || return 1
        shift 4
        echo sudoers-root-0440 >> "$TEST_ROOT/trace"
    fi
    command install "$@"
}
aws() {
    if [[ "$1" == --version ]]; then
        [[ ! -f "$TEST_ROOT/missing-cli" || -f "$TEST_ROOT/cli-installed" ]] || return 1
        echo aws-version >> "$TEST_ROOT/trace"
        echo 'aws-cli/2.test'
        return 0
    fi
    [[ -z "${AWS_ACCESS_KEY_ID:-}" && "$AWS_CONFIG_FILE" == /dev/null ]] || return 1
    [[ "$*" == 'ecr get-login-password --region us-east-1' ]] || return 1
    [[ ! -f "$TEST_ROOT/fail-login" ]] || return 1
    printf 'test-token'
}
docker() {
    echo "docker $*" >> "$TEST_ROOT/trace"
    case "$1" in
        login) cat >/dev/null ;;
        pull) [[ ! -f "$TEST_ROOT/fail-pull" ]] ;;
        container) [[ ! -f "$TEST_ROOT/exists" ]] || echo test-container ;;
        inspect) if [[ -f "$TEST_ROOT/running" ]]; then echo true; else echo false; fi ;;
        stop) rm -f "$TEST_ROOT/running" ;;
        rm) [[ ! -f "$TEST_ROOT/running" ]] || return 1; rm -f "$TEST_ROOT/exists" ;;
        run)
            [[ ! -f "$TEST_ROOT/fail-run" ]] || return 1
            printf '%s\n' "$@" > "$TEST_ROOT/run-args"
            touch "$TEST_ROOT/exists" "$TEST_ROOT/running" ;;
    esac
}
curl() {
    if [[ "$*" == *awscli.amazonaws.com* ]]; then
        echo "cli-download $*" >> "$TEST_ROOT/trace"
        [[ ! -f "$TEST_ROOT/fail-download" ]]
        return
    fi
    echo curl >> "$TEST_ROOT/trace"
    [[ ! -f "$TEST_ROOT/fail-health" ]] || return 28
    if [[ -f "$TEST_ROOT/health-body" ]]; then cat "$TEST_ROOT/health-body"; else echo '{"liveness": "ok"}'; fi
}
# Use Python's JSON parser as the offline jq stand-in; production uses installed jq.
jq() {
    "$TEST_PYTHON" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)=={"liveness":"ok"} else 1)'
}
'''


@unittest.skipUnless(BASH, "Bash required for bootstrap workflow tests")
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".bootstrap-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = ROOT.joinpath("bootstrap.sh").read_text(encoding="utf-8")
        sandboxed = self.script
        for old, new in {
            "/var/log/bootstrap.log": "${TEST_ROOT}/bootstrap.log",
            "/run/cloud-security-posture-bootstrap.lock": "${TEST_ROOT}/bootstrap.lock",
            "/etc/cloud-security-posture.env": "${TEST_ROOT}/posture.env",
            "/etc/sudoers.d": "${TEST_ROOT}/sudoers.d",
        }.items():
            sandboxed = sandboxed.replace(old, new)
        self.root.joinpath("run.sh").write_text(FAKES + sandboxed, newline="\n")

    def run_bootstrap(self, **overrides):
        import sys
        env = dict(os.environ, TEST_ROOT=self.root.relative_to(ROOT).as_posix(),
                   TEST_PYTHON=Path(sys.executable).as_posix(), IMAGE_TAG="1.1")
        env.update(overrides)
        return subprocess.run([BASH, self.root.joinpath("run.sh").as_posix()],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shell_syntax_and_no_host_build(self):
        result = subprocess.run([BASH, "-n", ROOT.joinpath("bootstrap.sh").as_posix()],
                                capture_output=True, text=True, timeout=10)
        self.assert_success(result)
        for removed in ("git clone", "pip install", "python3-venv", "ExecStart=", "chown -hR"):
            self.assertNotIn(removed, self.script)
        self.assertEqual(self.script.count("1.1"), 1)

    def test_existing_cli_skips_installer_and_logs_version(self):
        self.assert_success(self.run_bootstrap())
        trace = self.root.joinpath("trace").read_text()
        self.assertNotIn("cli-download", trace)
        self.assertNotIn("cli-install", trace)
        self.assertIn("aws-version", trace)
        apt_lines = [line for line in trace.splitlines() if line.startswith("apt ")]
        self.assertNotIn("awscli", " ".join(apt_lines))
        self.assertIn("aws-cli/2.test", self.root.joinpath("bootstrap.log").read_text())

    def test_missing_cli_installs_official_x86_archive_once(self):
        self.root.joinpath("missing-cli").touch()
        self.assert_success(self.run_bootstrap())
        self.assert_success(self.run_bootstrap())
        trace = self.root.joinpath("trace").read_text()
        self.assertEqual(trace.count("cli-download"), 1)
        self.assertIn("https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip", trace)
        self.assertEqual(trace.count("cli-install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli --update"), 1)
        self.assertEqual(trace.count("aws-version"), 2)
        self.assertFalse(self.root.joinpath("work").exists())

    def test_missing_cli_selects_arm64_archive(self):
        self.root.joinpath("missing-cli").touch()
        self.assert_success(self.run_bootstrap(TEST_ARCH="aarch64"))
        self.assertIn("awscli-exe-linux-aarch64.zip", self.root.joinpath("trace").read_text())

    def test_cli_installation_failures_do_not_replace_container(self):
        self.root.joinpath("missing-cli").touch()
        for failure in ("fail-download", "fail-unzip", "fail-install"):
            with self.subTest(failure=failure):
                marker = self.root.joinpath(failure)
                marker.touch()
                self.assertNotEqual(self.run_bootstrap().returncode, 0)
                trace = self.root.joinpath("trace").read_text()
                self.assertNotIn("docker login", trace)
                self.assertNotIn("docker rm", trace)
                self.assertFalse(self.root.joinpath("work").exists())
                marker.unlink()

    def test_fresh_and_repeat_runs(self):
        self.assert_success(self.run_bootstrap(AWS_ACCESS_KEY_ID="must-not-use"))
        config = 'AWS_REGION=us-east-1\nALLOWED_INSTANCE_NAME_TAGS="lab app"\nAWS_ACCESS_KEY_ID=must-not-pass\n'
        self.root.joinpath("posture.env").write_text(config)
        self.assert_success(self.run_bootstrap(IMAGE_TAG="release-2"))
        self.assertEqual(self.root.joinpath("posture.env").read_text(), config)
        trace = self.root.joinpath("trace").read_text()
        self.assertEqual(trace.count("docker-member"), 2)
        self.assertEqual(trace.count("sudoers-root-0440"), 2)
        self.assertIn("docker stop --time 20 test-container", trace)
        self.assertIn("docker rm test-container", trace)
        self.assertEqual(self.root.joinpath("sudoers.d/ssm-user").read_text(),
                         "ssm-user ALL=(ALL) NOPASSWD:ALL\n")
        args = self.root.joinpath("run-args").read_text().splitlines()
        for expected in ("HOST=0.0.0.0", "8000:8000", "unless-stopped", "journald",
                         "ALLOWED_INSTANCE_NAME_TAGS=lab app", "AWS_REGION=us-east-1"):
            self.assertIn(expected, args)
        self.assertNotIn("must-not-pass", " ".join(args))
        self.assertEqual(args[-1], "980468996827.dkr.ecr.us-east-1.amazonaws.com/cloud-security-posture-explorer:release-2")
        log = self.root.joinpath("bootstrap.log").read_text()
        for message in ("Docker installed", "Docker started", "ECR login successful", "Image pulled",
                        "Container removed", "Container started", "Health check passed"):
            self.assertIn(message, log)

    def test_stopped_container_and_legacy_service_migration(self):
        self.root.joinpath("exists").touch()
        self.root.joinpath("legacy").touch()
        self.assert_success(self.run_bootstrap())
        trace = self.root.joinpath("trace").read_text()
        self.assertNotIn("docker stop", trace)
        self.assertIn("docker rm test-container", trace)
        self.assertLess(trace.index("docker pull"), trace.index("systemctl disable --now"))
        self.assertLess(trace.index("systemctl disable --now"), trace.index("docker run"))

    def test_login_and_pull_failures_preserve_running_container(self):
        for failure in ("fail-login", "fail-pull"):
            with self.subTest(failure=failure):
                marker = self.root.joinpath(failure)
                marker.touch()
                self.root.joinpath("exists").touch()
                self.root.joinpath("running").touch()
                result = self.run_bootstrap()
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(self.root.joinpath("running").exists())
                self.assertNotIn("docker rm", self.root.joinpath("trace").read_text())
                marker.unlink()

    def test_container_start_failure_is_fatal(self):
        self.root.joinpath("fail-run").touch()
        self.assertNotEqual(self.run_bootstrap().returncode, 0)
        self.assertNotIn("Health check passed", self.root.joinpath("bootstrap.log").read_text())

    def test_health_timeout_is_fatal(self):
        self.root.joinpath("fail-health").touch()
        self.assertNotEqual(self.run_bootstrap().returncode, 0)
        trace = self.root.joinpath("trace").read_text().splitlines()
        self.assertEqual(trace.count("curl"), 30)

    def test_wrong_health_body_is_fatal(self):
        self.root.joinpath("health-body").write_text('{"liveness":"failed"}')
        self.assertNotEqual(self.run_bootstrap().returncode, 0)

    def test_invalid_tag_fails_before_installation(self):
        self.assertNotEqual(self.run_bootstrap(IMAGE_TAG="bad/tag").returncode, 0)
        self.assertFalse(self.root.joinpath("trace").exists())

    def test_sudoers_validation_failure_preserves_existing_rule(self):
        self.root.joinpath("sudoers.d").mkdir()
        rule = self.root.joinpath("sudoers.d/ssm-user")
        rule.write_text("existing rule\n")
        self.root.joinpath("reject-sudoers").touch()
        self.assertNotEqual(self.run_bootstrap().returncode, 0)
        self.assertEqual(rule.read_text(), "existing rule\n")
