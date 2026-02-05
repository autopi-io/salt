import os
import shutil
import subprocess
import sys

import pytest
from saltfactories.utils import random_string

import salt.utils.files
import salt.utils.user


@pytest.fixture(scope="module")
def salt_call_wrapper():
    # Create a wrapper script for salt-call
    wrapper_path = "/tmp/salt-call-wrapper"
    salt_root = os.getcwd()

    # We need to make sure the wrapper uses the same python environment
    # and has the salt package in path.
    # sys.executable should be the venv python.

    with salt.utils.files.fopen(wrapper_path, "w") as f:
        f.write(
            f"""#!{sys.executable}
import sys
sys.path.insert(0, "{salt_root}")
from salt.scripts import salt_call
if __name__ == '__main__':
    salt_call()
"""
        )
    os.chmod(wrapper_path, 0o755)

    # Symlink to /usr/local/bin/salt-call using sudo
    # We check if it exists first to avoid overwriting
    if os.path.exists("/usr/local/bin/salt-call"):
        os.remove(wrapper_path)
        pytest.skip("/usr/local/bin/salt-call already exists, skipping test")

    try:
        subprocess.run(
            ["sudo", "ln", "-s", wrapper_path, "/usr/local/bin/salt-call"], check=True
        )
    except subprocess.CalledProcessError:
        os.remove(wrapper_path)
        pytest.skip("Failed to create symlink /usr/local/bin/salt-call with sudo")

    yield

    # Cleanup
    try:
        subprocess.run(["sudo", "rm", "-f", "/usr/local/bin/salt-call"], check=True)
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    if os.path.exists(wrapper_path):
        os.remove(wrapper_path)


@pytest.fixture(scope="module")
def sudo_minion(salt_master, salt_factories, salt_call_wrapper):
    # Configure minion with current user as 'user' (so it normally runs as this user)
    # But 'sudo_user' as 'root' (so it uses sudo to run commands)
    config_overrides = {
        "sudo_user": "root",
        "user": salt.utils.user.get_user(),
    }

    factory = salt_master.salt_minion_daemon(
        random_string("sudo-minion-"),
        overrides=config_overrides,
    )
    with factory.started():
        yield factory


@pytest.mark.skipif(shutil.which("sudo") is None, reason="sudo is not available")
def test_sudo_executor_runs_as_root(sudo_minion, salt_cli):
    """
    Test that when sudo_user is set to root, salt-call runs as root.
    This validates that privileges were NOT dropped to the minion's configured user.
    """
    # Verify that we can run a command via sudo executor
    # We expect 'id -u' to return 0 (root) because we configured sudo_user: root
    # If the fix is missing, salt-call would see "user: <current_user>" in config
    # and drop privileges to that user, so 'id -u' would return <current_user_uid>.

    ret = salt_cli.run("cmd.run", "id -u", minion_tgt=sudo_minion.id)
    assert ret.returncode == 0

    # Check if the output is 0.
    # Note: ret.data might be parsed as int or string depending on outputter,
    # but cmd.run usually returns string.

    # We need to handle potential newlines or whitespace
    uid = ret.data.strip() if isinstance(ret.data, str) else str(ret.data)

    assert (
        uid == "0"
    ), f"Expected uid 0 (root), got {uid}. salt-call likely dropped privileges."
