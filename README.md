# infra-faust
This repository contains configuration and tooling for my personal cloud infrastructure.
At the moment, this primarily consist of a two node kubernetes cluster running on bare metal
dedicated machines hosted at a Hetzner datacenter. The nodes are connected on a private
virtual L2 network.

**Spec:** 256GB DDR4 Memory, 64 CPU, 8TB NVMe disk, 10 Gbps  
**Components:**
* **Package Manager:** Helm
* **Networking:** Calico, Istio, MetalLB
* **Storage:** Rook Ceph
* **Observability:** Prometheus, Loki, Jaeger, Promtail, Grafana
* **Database:** CockroachDB
* **PKI:** Step CA, cert-manager

Tailscale is used to remotely `kubectl` into the cluster. Administrative internal services are exposed behind mTLS.

> **Note:** This setup is nothing more than to deploy my hobby projects. It's more than likely overkill.
I'm most definitely hemorrhaging money from overprovisioning resources haha.

## Docs
#### Kubernetes
* [Node setup](docs/kubernetes.md#node-setup)
* [Cluster bootstrap](docs/kubernetes.md#cluster-bootstrap)
* [Joining a Node](docs/kubernetes.md#joining-a-node)

#### Misc
* [PKI (Certificates)](docs/pki.md)
