import os

from pyinfra import host
from pyinfra.facts.files import Link
from pyinfra.operations import apt, server

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
    name="Install APT packages",
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
    ],
    _sudo=True,
)

server.shell(
    name="Install k3s",
    commands=[
        "curl -sfL https://get.k3s.io | sh -s - --disable=traefik,servicelb",
    ],
)
