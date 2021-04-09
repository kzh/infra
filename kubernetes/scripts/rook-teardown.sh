#!/usr/bin/env bash

devices=(nvme0n1p3 nvme1n1p3)

for device in "${devices[@]}"; do
    DISK="/dev/${device}"
    sgdisk --zap-all $DISK
    blkdiscard $DISK
done

ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %
rm -rf /dev/ceph-*

rm -rf /var/lib/rook
