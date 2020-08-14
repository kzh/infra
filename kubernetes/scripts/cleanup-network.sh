#!/usr/bin/env bash

iptables -F && iptables -t nat -F && iptables -t mangle -F && iptables -X

rm -rf /etc/cni/net.d
