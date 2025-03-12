from io import StringIO
import os

from pyinfra import host
from pyinfra.facts.files import Link
from pyinfra.facts.hardware import Ipv4Addrs
from pyinfra.operations import apt, server, files

server.shell(
    name="Install tailscale",
    commands=[
        # udp throughput improvement (https://tailscale.com/kb/1320/performance-best-practices#linux-optimizations-for-subnet-routers-and-exit-nodes)
        "ethtool -K $(ip -o route get 8.8.8.8 | cut -f 5 -d ' ') rx-udp-gro-forwarding on rx-gro-list off",
        "printf '#!/bin/sh\n\nethtool -K %s rx-udp-gro-forwarding on rx-gro-list off \n' \"$(ip -o route get 8.8.8.8 | cut -f 5 -d ' ')\" | tee /etc/networkd-dispatcher/routable.d/50-tailscale",
        "chmod 755 /etc/networkd-dispatcher/routable.d/50-tailscale",
        "/etc/networkd-dispatcher/routable.d/50-tailscale && test $? -eq 0 || echo 'An error occurred.'",
        # ip forwarding (https://tailscale.com/kb/1019/subnets?tab=linux#enable-ip-forwarding)
        "echo 'net.ipv4.ip_forward = 1' | tee -a /etc/sysctl.d/99-tailscale.conf",
        "echo 'net.ipv6.conf.all.forwarding = 1' | tee -a /etc/sysctl.d/99-tailscale.conf",
        "sysctl -p /etc/sysctl.d/99-tailscale.conf",
        # tailscale
        "curl -fsSL https://tailscale.com/install.sh | sh",
        f"tailscale up --auth-key {os.getenv('TAILSCALE_AUTH_KEY')} --ssh --advertise-exit-node",
    ],
    _sudo=True,
)

link_info = host.get_fact(Link, "/etc/resolv.conf")
if not link_info:
    server.shell(
        name="",
        commands=[
            "chattr -i /etc/resolv.conf",
            "ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf",
        ],
        _sudo=True,
    )

apt.ppa(name="Add PPA for fastfetch", src="ppa:zhangsongcui3371/fastfetch")

apt.packages(
    name="Install apt packages",
    packages=["vim", "htop", "fastfetch", "fzf", "ripgrep"],
    update=True,
    _sudo=True,
)

server.shell(
    name="Configure UFW",
    commands=[
        "ufw --force enable",
        "ufw default deny incoming",
        "ufw default allow routed",
        "ufw default allow outgoing",
        "ufw allow in on tailscale0",
        "ufw allow in 22/tcp",
        "ufw allow 6443/tcp",
        "ufw allow from 10.42.0.0/16 to any",
        "ufw allow from 10.43.0.0/16 to any",
    ],
    _sudo=True,
)

server.shell(
    name="Install k3s",
    commands=[
        "curl -sfL https://get.k3s.io | sh -s - --disable-kube-proxy --disable=traefik,servicelb --flannel-backend=none --disable-network-policy",
    ],
)

ipv4_addresses = host.get_fact(Ipv4Addrs)
print(ipv4_addresses)

files.put(
    name="Copy Cilium helm config",
    src=StringIO(f"""
kubeProxyReplacement: true
ipam.operator.clusterPoolIPv4PodCIDRList: "10.42.0.0/16"
k8sServiceHost: {ipv4_addresses["eth0"][0]}
k8sServicePort: 6443
securityContext.privileged: true
"""),
    dest="cilium-config.yaml",
)

server.shell(
    name="Install Cilium",
    commands=[
        """
CILIUM_CLI_VERSION=$(curl -s https://raw.githubusercontent.com/cilium/cilium-cli/main/stable.txt)
CLI_ARCH=amd64
if [ "$(uname -m)" = "aarch64" ]; then CLI_ARCH=arm64; fi
curl -L --fail --remote-name-all https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CLI_ARCH}.tar.gz{,.sha256sum}
sha256sum --check cilium-linux-${CLI_ARCH}.tar.gz.sha256sum
sudo tar xzvfC cilium-linux-${CLI_ARCH}.tar.gz /usr/local/bin
rm cilium-linux-${CLI_ARCH}.tar.gz cilium-linux-${CLI_ARCH}.tar.gz.sha256sum
""",
        "KUBECONFIG=/etc/rancher/k3s/k3s.yaml cilium install --values cilium-config.yaml --wait",
        "rm cilium-config.yaml",
    ],
)
