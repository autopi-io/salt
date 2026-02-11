import logging
import time

import packaging.version
import pytest

pytestmark = [
    pytest.mark.skip_unless_on_linux(reason="Only supported on Linux family"),
]

log = logging.getLogger(__name__)


@pytest.fixture
def salt_install_env():
    """
    Override the default install environment to set SALT_MINION_USER=salt.

    This causes the fresh installation to create directories owned by salt:salt
    instead of the default root:root.
    """
    return {
        "SALT_MINION_USER": "salt",
        "SALT_MINION_GROUP": "salt",
    }


def test_salt_user_ownership_preserved_on_upgrade(
    call_cli, install_salt_systemd, salt_systemd_setup
):
    """
    Test that salt user ownership is preserved during upgrade when no env vars are set.

    This test verifies:
    1. Fresh install with SALT_MINION_USER=salt creates salt:salt ownership
    2. Upgrade WITHOUT environment variables preserves the salt:salt ownership
    3. The RPM %posttrans scriptlet correctly detects and preserves existing ownership

    Test flow:
    - Initial install happens with SALT_MINION_USER=salt (via salt_install_env fixture)
    - Verify directories are owned by salt:salt
    - Upgrade to new version WITHOUT setting any environment variables
    - Verify directories are still owned by salt:salt (ownership preserved)
    """
    # Skip if this is not an upgrade test
    if not install_salt_systemd.upgrade:
        pytest.skip("This test requires upgrade testing, run with --upgrade")

    upgrade_version = packaging.version.parse(install_salt_systemd.artifact_version)

    # Verify we have a previous version installed
    ret = call_cli.run("--local", "test.version")
    assert ret.returncode == 0
    installed_version = packaging.version.parse(ret.data)

    # If we're already at or above the upgrade version, install previous version first
    if installed_version >= upgrade_version:
        log.info("Installing previous version before testing upgrade")
        install_salt_systemd.install_previous(downgrade=True)
        ret = call_cli.run("--local", "test.version")
        assert ret.returncode == 0
        installed_version = packaging.version.parse(ret.data)
        assert installed_version < upgrade_version

    log.info("Testing upgrade from %s to %s", installed_version, upgrade_version)

    # The previous version doesn't support SALT_MINION_USER environment variable,
    # so we need to manually set up salt:salt ownership AND user configuration
    # to simulate a system that was installed with salt user configuration.

    # First, ensure salt user exists
    ret = call_cli.run("--local", "user.info", "salt")
    if ret.returncode != 0 or not ret.data:
        log.info("Creating salt user for testing")
        ret = call_cli.run("--local", "user.add", "salt", system=True, createhome=False)
        assert ret.returncode == 0

    # Configure minion to run as salt user
    log.info("Configuring minion to run as salt user")
    ret = call_cli.run(
        "--local",
        "cmd.run",
        "mkdir -p /etc/salt/minion.d && echo 'user: salt' > /etc/salt/minion.d/user.conf",
    )
    assert ret.returncode == 0

    # Restart minion to apply the user configuration
    log.info("Restarting minion to apply user configuration")
    ret = call_cli.run(
        "--local", "--priv=root", "cmd.run", "systemctl restart salt-minion"
    )
    assert ret.returncode == 0
    time.sleep(5)  # Wait for minion to restart

    # Define the minion directories
    minion_dirs = [
        "/etc/salt/pki/minion",
        "/var/cache/salt/minion",
        "/var/log/salt",
        "/var/run/salt/minion",
    ]

    # After restarting with user: salt, the minion should have created directories
    # as salt:salt. But if they were previously root:root, manually change them.
    log.info("Ensuring salt:salt ownership on minion directories")
    for dir_path in minion_dirs:
        ret = call_cli.run(
            "--local", "--priv=root", "cmd.run", f"chown -R salt:salt {dir_path}"
        )
        if ret.returncode != 0:
            log.warning("Failed to chown %s, directory may not exist yet", dir_path)

    log.info("Verifying pre-upgrade ownership is salt:salt")
    for dir_path in minion_dirs:
        test_cmd = f"ls -ld {dir_path}"
        ret = call_cli.run("--local", "cmd.run", test_cmd)
        if ret.returncode != 0:
            log.warning("Directory %s does not exist, skipping", dir_path)
            continue

        # Use ret.data instead of ret.stdout to get the actual command output
        # ls -ld output format: perms links user group size date time name
        parts = ret.data.strip().split()
        test_user = parts[2]
        test_group = parts[3]

        assert (
            test_user == "salt"
        ), f"Before upgrade: Expected {dir_path} owned by salt, got {test_user}. Full output: {ret.data}"
        assert (
            test_group == "salt"
        ), f"Before upgrade: Expected {dir_path} group salt, got {test_group}. Full output: {ret.data}"

    # Now upgrade WITHOUT setting environment variables
    # The RPM %posttrans scriptlet should detect existing ownership and preserve it
    log.info("Upgrading to version %s WITHOUT environment variables", upgrade_version)
    install_salt_systemd.install(upgrade=True)
    time.sleep(10)  # Allow time for services to restart

    # Verify we upgraded successfully
    ret = call_cli.run("--local", "--priv=root", "test.version")
    assert ret.returncode == 0
    installed_version = packaging.version.parse(ret.data)
    assert (
        installed_version == upgrade_version
    ), f"Expected version {upgrade_version}, got {installed_version}"

    # Verify that ownership is STILL salt:salt (preserved during upgrade)
    log.info("Verifying post-upgrade ownership is still salt:salt")
    for dir_path in minion_dirs:
        test_cmd = f"ls -ld {dir_path}"
        ret = call_cli.run("--local", "--priv=root", "cmd.run", test_cmd)
        if ret.returncode != 0:
            log.warning("Directory %s does not exist, skipping", dir_path)
            continue

        # Use ret.data instead of ret.stdout to get the actual command output
        parts = ret.data.strip().split()
        test_user = parts[2]
        test_group = parts[3]

        assert (
            test_user == "salt"
        ), f"After upgrade: Expected {dir_path} owned by salt, got {test_user}. Ownership was not preserved! Full output: {ret.data}"
        assert (
            test_group == "salt"
        ), f"After upgrade: Expected {dir_path} group salt, got {test_group}. Ownership was not preserved! Full output: {ret.data}"

    log.info("SUCCESS: salt:salt ownership was preserved during upgrade")

    # Now verify that running salt-call and salt-pip as root still preserves salt user ownership
    # Both commands should drop privileges to the configured user and not create root-owned files
    log.info("Testing that salt-call run as root preserves salt:salt ownership")

    # Run a salt-call command that will access cache
    ret = call_cli.run("--local", "--priv=root", "test.ping")
    assert ret.returncode == 0

    # Run salt-pip directly to install a package into the 'extras' directory
    # This verifies that salt-pip drops privileges and creates files owned by salt:salt
    log.info("Installing package via salt-pip to test extras directory ownership")
    ret = call_cli.run("--local", "--priv=root", "cmd.run", "salt-pip install cowsay")
    assert ret.returncode == 0

    # Now verify NO files in the cache directories are owned by root
    log.info("Verifying no root-owned files were created in salt user directories")
    for dir_path in minion_dirs:
        # Find all files in the directory and check ownership
        test_cmd = f"find {dir_path} -type f -uid 0 2>/dev/null || true"
        ret = call_cli.run("--local", "--priv=root", "cmd.run", test_cmd)

        if ret.stdout.strip():
            # Found root-owned files!
            root_owned_files = ret.stdout.strip().split("\n")
            pytest.fail(
                f"Found root-owned files in {dir_path} after running salt-call/salt-pip:\n"
                + "\n".join(root_owned_files[:10])  # Show first 10 files
                + f"\n... ({len(root_owned_files)} total root-owned files)"
            )

    log.info(
        "SUCCESS: No root-owned files created, salt-call and salt-pip properly dropped privileges"
    )
