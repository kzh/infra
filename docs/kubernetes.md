## Kubernetes

### Node setup
> Note: This manual is geared towards the machines (BV2) I rent from OVH.

**Housecleaning**
```
$ sudo apt update
$ sudo apt upgrade
```

**Disabling Swap**
```
$ sudo swapoff -a
# Remove swap mounts from /etc/fstab
$ sudo vim /etc/fstab
```

**Creating Partitions for Ceph**  
This step requires the machine to be booted in rescue mode.
```
# Use fdisk to remove the swap partitions and create new ones.
$ sudo fdisk /dev/sda
$ sudo fdisk /dev/sdb 
```
 
**Removing Snapd**
```
$ sudo snap remove lxd core18 snapd
$ sudo apt purge snapd
$ sudo rm -rf /snap
```

**Tidy DNS**
```
# Remove `nameserver: 127.0.0.1` from /etc/resolv.conf
$ sudo vim /etc/resolv.conf

# Remove local DNS server (since we're using kubernetes' coredns)
$ sudo apt purge bind9
```

**Setting up NIC for OVH VLAN**
```
$ sudo apt install vlan
$ sudo modprobe 8021q
$ sudo echo "8021q" >> /etc/modules

$ sudo vim /etc/netplan/01-netcfg.yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    eno2: {}

  vlans:
    vlan.99:
      id: 99
      link: eno2
      addresses: [192.168.200.1/24]

$ sudo netplan apply
```

**Setting up Tailscale**
```
$ curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/focal.gpg | sudo apt-key add -
$ curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/focal.list | sudo tee /etc/apt/sources.list.d/tailscale.list
$ sudo apt-get update
$ sudo apt-get install tailscale

# Note: Do this only on the first node.
$ sudo tailscale up --advertise-routes=192.168.200.0/24

# Otherwise:
$ sudo tailscale up

```

**Installing Docker**
```
$ sudo apt-get install \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg-agent \
    software-properties-common

$ curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
$ sudo add-apt-repository \
     "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
     $(lsb_release -cs) \
     stable"
$ sudo apt-get update
$ sudo apt-get install docker-ce docker-ce-cli containerd.io

# Manager Docker as a non-root user
$ sudo usermod -aG docker $USER
$ newgrp docker

# Configure cgroup driver to systemd
$ sudo vim /lib/systemd/system/docker.service
# Add `--exec-opt native.cgroupdriver=systemd` to the `dockerd` command
$ sudo systemctl daemon-reload
$ sudo systemctl restart docker
```

**Installing Kubeadm**
```
$ sudo modprobe br_netfilter
$ sudo echo "br_netfilter" >> /etc/modules 

$ cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-ip6tables = 1
net.bridge.bridge-nf-call-iptables = 1
EOF
$ sudo sysctl --system

$ sudo apt-get update && sudo apt-get install -y apt-transport-https curl
$ curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key add -
$ cat <<EOF | sudo tee /etc/apt/sources.list.d/kubernetes.list
deb https://apt.kubernetes.io/ kubernetes-xenial main
EOF
$ sudo apt-get update
$ sudo apt-get install -y kubelet kubeadm kubectl
$ sudo apt-mark hold kubelet kubeadm kubectl

$ sudo echo "KUBELET_EXTRA_ARGS=--node-ip=192.168.200.1" >> /etc/default/kubelet
$ sudo systemctl daemon-reload
$ sudo systemctl restart kubelet 
```

### Cluster Bootstrap
```
$ kubeadm init --config kubernetes/kubeadm/init.yaml
```

**Calico CNI**
```
$ kubectl apply -f kubernetes/resources/calico.yaml
```

**Installing Helm**
```
$ curl https://baltocdn.com/helm/signing.asc | sudo apt-key add -
$ sudo apt-get install apt-transport-https --yes
$ echo "deb https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list
$ sudo apt-get update
$ sudo apt-get install helm
```

**Rook Ceph cluster**
```
$ helm repo add rook-release https://charts.rook.io/release
$ helm repo update
$ kubectl create ns rook-ceph
$ helm install --namespace rook-ceph rook-ceph rook-release/rook-ceph

$ kubectl apply -f kubernetes/resources/rook-ceph.yaml

# Set default storage class
$ kubectl patch storageclass rook-ceph-block -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

**MetalLB**
```
$ kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.9.5/manifests/namespace.yaml
$ kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.9.5/manifests/metallb.yaml
$ kubectl create secret generic -n metallb-system memberlist --from-literal=secretkey="$(openssl rand -base64 128)"

# Configure the address pool
$ kubectl apply -f kubernetes/resources/metallb.yaml
```

**cert-manager**
```
$ kubectl create namespace cert-manager
$ helm repo add jetstack https://charts.jetstack.io
$ helm repo update
$ helm install \
  cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.0.4 \
  --set installCRDs=true

# Create letsencrypt cluster cert issuer
$ kubectl apply -f kubernetes/secrets/cloudflare-dns.yaml
$ kubectl apply -f kubernetes/resources/cert-manager.yaml
```

**Istio**
```
$ curl -L https://istio.io/downloadIstio | sh -
$ cd istio-*
$ ./bin/istioctl install
```

### Joining a Node
```
# Run this command on a connected node. It will generate
# a bash command that you can then run on an unconnected node
# to join.
$ kubeadm token create --print-join-command
```