# infra-faust
This repository contains configuration and tooling for my personal cloud infrastructure.
At the moment, this primarily consist of a two node kubernetes cluster running on bare metal
dedicated machines hosted at a Hetzner datacenter. The nodes are connected on a private
virtual L2 network.

**Spec:** 256GB DDR4 RAM, 64 CPU (2x AMD Radeon 9 5950X), 8TB NVMe disk, 10 Gbps  
**Components:**
* **IaC:** Pulumi
* **Package Manager:** Helm
* **Networking:** Calico, MetalLB, Istio
* **Storage:** Rook Ceph
* **Monitoring:** Prometheus, Grafana, Jaeger, OpenTelemetry Collector, Fluent Bit, Elasticsearch, Kibana
* **Database:** CockroachDB
* **PKI:** Vault, Secrets Store CSI, cert-manager

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
