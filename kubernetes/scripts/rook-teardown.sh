#!/usr/bin/env bash

devices=(a3 b3 c d)

for device in "${devices[@]}"; do
    DISK="/dev/sd${device}"
    sgdisk --zap-all $DISK
    dd if=/dev/zero of="$DISK" bs=1M count=100 oflag=direct,dsync
    blkdiscard $DISK
done

ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %
rm -rf /dev/ceph-*

rm -rf /var/lib/rook
