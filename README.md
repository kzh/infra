# infra-faust
This repository contains configuration and tooling for my personal cloud infrastructure.
At the moment, this primarily consist of a two node kubernetes cluster running on bare metal
dedicated machines hosted at an OVH datacenter. The nodes are connected on a private OVH vlan 
network.

**Spec:** 512 GB Memory, 64 CPU, 16TB disk, 500 Mbps  
**Components:**
* **Package Manager:** Helm
* **Networking:** Calico, Istio, MetalLB
* **Storage:** Rook Ceph
* **Observability:** Prometheus, Loki, Jaeger, Promtail, Grafana
* **Database:** CockroachDB
* **PKI:** cert-manager

Tailscale is used to remotely `kubectl` into the cluster. Administrative internal services are exposed behind mTLS.

> **Note:** This setup is nothing more than to deploy my hobby projects. It's more than likely overkill.
I'm most definitely leaking money from overprovisioning resources haha.

## Docs 
#### Kubernetes
* [Node setup](docs/kubernetes.md#node-setup)
* [Cluster bootstrap](docs/kubernetes.md#cluster-bootstrap)
* [Joining a Node](docs/kubernetes.md#joining-a-node)

#### Misc
* PKI (Certificates)