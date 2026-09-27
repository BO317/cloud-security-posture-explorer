"""Execute bootstrap control flow with host/network mutations replaced by fakes.

These tests check fresh/repeat deployment behavior, not Linux UID enforcement.
"""
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
apt-get() { :; }
flock() { :; }
systemctl() { [[ "$1" != is-active ]]; }
chown() {
    [[ "$1" == -hR && "$2" == ssm-user:ssm-user ]] || return 1
    touch "$APP_DIR/.owned"
    echo repaired >> "$TEST_ROOT/trace"
}
runuser() {
    [[ "$1" == -u && "$2" == ssm-user && "$3" == -- ]] || return 1
    shift 3
    FAKE_USER=ssm-user "$@"
}
git() {
    if [[ "$1" == clone ]]; then
        mkdir -p "$APP_DIR/.git" "$APP_DIR/app"
        touch "$APP_DIR/app/requirements-lock.txt"
        echo cloned >> "$TEST_ROOT/trace"
    else
        # Simulate Git's ownership check on rerun, plus an existing root checkout.
        [[ "$FAKE_USER" == ssm-user && -f "$APP_DIR/.owned" ]] || return 1
        if [[ "$3" == remote ]]; then echo "$REPOSITORY"; else echo test-revision; fi
        echo git-as-owner >> "$TEST_ROOT/trace"
    fi
}
python3() {
    [[ "$FAKE_USER" == ssm-user ]] || return 1
    mkdir -p "$APP_DIR/.venv/bin"
    cat > "$APP_DIR/.venv/bin/python" <<'PYTHON'
#!/bin/bash
if [[ "$1" == -m ]]; then
    [[ "$FAKE_USER" == ssm-user ]] || exit 1
    echo pip-as-owner >> "$TEST_ROOT/trace"
else
    cat >/dev/null
fi
PYTHON
    chmod +x "$APP_DIR/.venv/bin/python"
}
'''


@unittest.skipUnless(BASH, "Bash required for bootstrap workflow tests")
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".bootstrap-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = ROOT.joinpath("bootstrap.sh").read_text()
        self.sandboxed = self.script.replace("/opt/cloud-security-posture-explorer", "${TEST_ROOT}/repo")
        self.sandboxed = self.sandboxed.replace("/var/log/bootstrap.log", "${TEST_ROOT}/bootstrap.log")
        self.sandboxed = self.sandboxed.replace("/run/cloud-security-posture-bootstrap.lock", "${TEST_ROOT}/bootstrap.lock")
        self.sandboxed = self.sandboxed.replace("/etc/cloud-security-posture.env", "${TEST_ROOT}/posture.env")
        self.sandboxed = self.sandboxed.replace("/etc/systemd/system/", "${TEST_ROOT}/")
        self.root.joinpath("run.sh").write_text(FAKES + self.sandboxed, newline="\n")

    def run_bootstrap(self):
        env = dict(os.environ, TEST_ROOT=self.root.relative_to(ROOT).as_posix(), FAKE_USER="root")
        return subprocess.run([BASH, self.root.joinpath("run.sh").as_posix()],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)

    def test_shell_syntax(self):
        result = subprocess.run([BASH, "-n", ROOT.joinpath("bootstrap.sh").as_posix()],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_and_repeat_runs_preserve_user_and_existing_configuration(self):
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.root.joinpath("posture.env").write_text("PRESERVE_THIS_CONFIGURATION=true\n")
        # Simulate files left by an earlier root deployment before rerunning.
        self.root.joinpath("repo/.owned").unlink()
        second = self.run_bootstrap()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        trace = self.root.joinpath("trace").read_text().splitlines()
        self.assertEqual(trace.count("cloned"), 1)
        self.assertEqual(trace.count("git-as-owner"), 2)
        self.assertEqual(trace.count("pip-as-owner"), 4)
        self.assertEqual(trace[-1], "repaired")
        self.assertEqual(self.root.joinpath("posture.env").read_text(), "PRESERVE_THIS_CONFIGURATION=true\n")
        unit = self.root.joinpath("cloud-security-posture-explorer.service").read_text()
        self.assertIn("User=ssm-user\n", unit)
        self.assertIn("ProtectSystem=strict\n", unit)

    def test_existing_nonrepository_directory_is_not_overwritten(self):
        self.root.joinpath("repo").mkdir()
        self.root.joinpath("repo/keep.txt").write_text("keep")
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.root.joinpath("repo/keep.txt").read_text(), "keep")
        self.assertFalse(self.root.joinpath("repo/.owned").exists())
