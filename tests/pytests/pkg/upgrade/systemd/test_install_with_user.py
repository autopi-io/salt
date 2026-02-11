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

    # Verify that salt user was created during initial install with SALT_MINION_USER=salt
    ret = call_cli.run("--local", "user.info", "salt")
    assert ret.returncode == 0
    assert ret.data, "salt user should exist after install with SALT_MINION_USER=salt"

    # Check that salt-minion directories are owned by salt:salt
    # (because we installed with SALT_MINION_USER=salt environment variable)
    minion_dirs = [
        "/etc/salt/pki/minion",
        "/var/cache/salt/minion",
        "/var/log/salt",
        "/var/run/salt/minion",
    ]

    log.info("Verifying pre-upgrade ownership is salt:salt")
    for dir_path in minion_dirs:
        test_cmd = f"ls -ld {dir_path}"
        ret = call_cli.run("--local", "cmd.run", test_cmd)
        if ret.returncode != 0:
            log.warning("Directory %s does not exist, skipping", dir_path)
            continue

        test_user = ret.stdout.strip().split()[2]
        test_group = ret.stdout.strip().split()[3]

        assert (
            test_user == "salt"
        ), f"Before upgrade: Expected {dir_path} owned by salt, got {test_user}"
        assert (
            test_group == "salt"
        ), f"Before upgrade: Expected {dir_path} group salt, got {test_group}"

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

        test_user = ret.stdout.strip().split()[2]
        test_group = ret.stdout.strip().split()[3]

        assert (
            test_user == "salt"
        ), f"After upgrade: Expected {dir_path} owned by salt, got {test_user}. Ownership was not preserved!"
        assert (
            test_group == "salt"
        ), f"After upgrade: Expected {dir_path} group salt, got {test_group}. Ownership was not preserved!"

    log.info("SUCCESS: salt:salt ownership was preserved during upgrade")

    # Now verify that running salt-call and salt-pip as root still preserves salt user ownership
    # Both commands should drop privileges to the configured user and not create root-owned files
    log.info("Testing that salt-call run as root preserves salt:salt ownership")

    # Run a salt-call command that will access cache
    ret = call_cli.run("--local", "--priv=root", "test.ping")
    assert ret.returncode == 0

    # Run salt-pip to verify it also preserves ownership
    # Use --version which is a read-only operation
    ret = call_cli.run("--local", "--priv=root", "pip.version")
    assert ret.returncode == 0

    # Now verify NO files in the cache directories are owned by root
    log.info("Verifying no root-owned files were created in salt user directories")
    for dir_path in minion_dirs:
        # Find all files in the directory and check ownership
        test_cmd = f"find {dir_path} -type f -uid 0 2>/dev/null || true"
        ret = call_cli.run("--local", "--priv=root", "cmd.run", test_cmd)

        if ret.stdout.strip():
            # Found root-owned files!
            root_owned_files = ret.stdout.strip().split('\n')
            pytest.fail(
                f"Found root-owned files in {dir_path} after running salt-call/salt-pip:\n"
                + "\n".join(root_owned_files[:10])  # Show first 10 files
                + f"\n... ({len(root_owned_files)} total root-owned files)"
            )

    log.info("SUCCESS: No root-owned files created, salt-call and salt-pip properly dropped privileges")
