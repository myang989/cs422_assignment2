#!/bin/sh
# Run once on the Linux host (not inside Docker): the container shares the host kernel,
# so kernel modules and congestion-control sysctls must be set here.
set -e
sudo modprobe tcp_bbr
sudo sysctl -w net.ipv4.tcp_allowed_congestion_control="reno cubic bbr"
uname -r
sysctl net.ipv4.tcp_congestion_control net.ipv4.tcp_available_congestion_control \
       net.ipv4.tcp_allowed_congestion_control
