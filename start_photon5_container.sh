#!/bin/bash
set -e

CONTAINER_NAME="photon5-salt-test"
SALT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Creating Photon 5 container with systemd ==="

# Stop and remove existing container if it exists
docker rm -f $CONTAINER_NAME 2>/dev/null || true

# Create Docker network if it doesn't exist
if ! docker network ls | grep -q ip6net; then
    echo "Creating docker network: ip6net"
    docker network create \
        -o com.docker.network.driver.mtu=1500 \
        --ipv6 \
        --subnet 2001:db8::/64 \
        ip6net
fi

# Start Photon 5 container with systemd
echo "Starting container with systemd..."
docker create \
    --name=$CONTAINER_NAME \
    --privileged \
    --workdir=/salt \
    -v "$SALT_SOURCE_DIR:/salt" \
    --network ip6net \
    --platform linux/amd64 \
    --entrypoint /usr/lib/systemd/systemd \
    ghcr.io/saltstack/salt-ci-containers/testing:photon-5 \
    --systemd \
    --unit rescue.target

docker start $CONTAINER_NAME

# Wait for systemd to be ready
echo "Waiting for systemd to initialize..."
sleep 3

echo "Container started: $CONTAINER_NAME"
echo ""

# Install Salt from Broadcom packages repository
echo "=== Installing Salt from packages.broadcom.com ==="
docker exec $CONTAINER_NAME bash -c '
set -e

echo "Adding Salt repository..."
cat > /etc/yum.repos.d/salt.repo << "EOF"
[salt-repo]
name=Salt repo for RHEL/CentOS $releasever
baseurl=https://packages.broadcom.com/artifactory/saltproject-rpm/
enabled=1
gpgcheck=0
EOF

echo ""
echo "Installing salt-minion package..."
tdnf install -y salt-minion

echo ""
echo "Checking salt user was created..."
id salt

echo ""
echo "Salt minion version:"
salt-minion --version

echo ""
echo "Checking minion configuration..."
grep -E "^user:|^#user:" /etc/salt/minion || echo "No user configuration found in minion config"

echo ""
echo "Checking directory ownership..."
ls -ld /etc/salt/pki/minion
ls -ld /var/cache/salt/minion
ls -ld /var/log/salt
ls -ld /var/run/salt/minion

echo ""
echo "Checking systemd service..."
systemctl cat salt-minion.service
'

echo ""
echo "=== Container ready ==="
echo ""
echo "Container is running with systemd enabled"
echo ""
echo "To access the container:"
echo "  docker exec -it $CONTAINER_NAME bash"
echo ""
echo "To check systemd status:"
echo "  docker exec $CONTAINER_NAME systemctl status"
echo ""
echo "To start salt-minion service:"
echo "  docker exec $CONTAINER_NAME systemctl start salt-minion"
echo ""
echo "To stop and remove:"
echo "  docker rm -f $CONTAINER_NAME"
