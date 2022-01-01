package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/apiextensions"
	batchv1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/batch/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(DeployEFKStack)
}

func DeployEFKStack(ctx *pulumi.Context) error {
	es, err := NewElasticsearch(ctx)
	if err != nil {
		return nil
	}

	bootstrap, err := BootstrapElasticsearch(ctx, es)
	if err != nil {
		return nil
	}

	if err := NewFluentBit(ctx, bootstrap); err != nil {
		return err
	}
	if err := NewKibana(ctx, bootstrap); err != nil {
		return err
	}
	return nil
}

func NewElasticsearch(ctx *pulumi.Context) (*helm.Chart, error) {
	const (
		ResourceName = "elasticsearch"
		Repository   = "https://helm.elastic.co/"
		Chart        = "elasticsearch"
		ChartVersion = "7.16.2"

		Namespace = "monitoring"
	)

	return helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: pulumi.Map{
			"replicas":           pulumi.Int(1),
			"minimumMasterNodes": pulumi.Int(1),

			"extraEnvs": pulumi.MapArray{
				pulumi.Map{
					"name":  pulumi.String("discovery.type"),
					"value": pulumi.String("single-node"),
				},
				pulumi.Map{
					"name":  pulumi.String("cluster.initial_master_nodes"),
					"value": nil,
				},
			},

			"resources": pulumi.Map{
				"requests": pulumi.Map{
					"cpu":    pulumi.String("2000m"),
					"memory": pulumi.String("8Gi"),
				},
				"limits": pulumi.Map{
					"cpu":    pulumi.String("2000m"),
					"memory": pulumi.String("8Gi"),
				},
			},
		},
	})
}

func BootstrapElasticsearch(ctx *pulumi.Context, es *helm.Chart) (pulumi.Resource, error) {
	const (
		JobName = "elasticsearch-bootstrap"
		Image   = "ubuntu:21.10"

		Namespace = "monitoring"
	)

	bootstrap := `
apt update && apt install -y curl
curl -H 'Content-Type: application/json' -X PUT http://elasticsearch-master-headless:9200/_ilm/policy/mx -d '
{"policy":{"phases":{"delete":{"min_age":"10d","actions":{"delete":{}}}}}}'
curl -H 'Content-Type: application/json' -X PUT http://elasticsearch-master-headless:9200/_index_template/mx_logs -d '
{"index_patterns":["kubernetes*","node*"],"template":{"settings":{"number_of_replicas":0,"index.lifecycle.name":"mx"}}}'
`

	return batchv1.NewJob(ctx, JobName, &batchv1.JobArgs{
		Metadata: &metav1.ObjectMetaArgs{
			Name:      pulumi.String(JobName),
			Namespace: pulumi.String(Namespace),
		},
		Spec: batchv1.JobSpecArgs{
			Template: corev1.PodTemplateSpecArgs{
				Spec: corev1.PodSpecArgs{
					Containers: corev1.ContainerArray{
						&corev1.ContainerArgs{
							Name:  pulumi.String(JobName),
							Image: pulumi.String(Image),
							Command: pulumi.StringArray{
								pulumi.String("/bin/bash"),
								pulumi.String("-c"),
								pulumi.String(bootstrap),
							},
						},
					},
					RestartPolicy: pulumi.String("Never"),
				},
			},
		},
	}, pulumi.DependsOnInputs(es.Ready))
}

func NewKibana(ctx *pulumi.Context, bootstrap pulumi.Resource) error {
	const (
		ResourceName = "kibana"
		Repository   = "https://helm.elastic.co/"
		Chart        = "kibana"
		ChartVersion = "7.16.2"

		Namespace = "monitoring"
	)

	_, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: pulumi.Map{
			"resources": pulumi.Map{
				"requests": pulumi.Map{
					"cpu": pulumi.String("2000m"),
				},
				"limits": pulumi.Map{
					"cpu": pulumi.String("2000m"),
				},
			},
		},
	}, pulumi.DependsOn([]pulumi.Resource{bootstrap}))
	if err != nil {
		return err
	}

	_, err = apiextensions.NewCustomResource(ctx,
		ResourceName,
		&apiextensions.CustomResourceArgs{
			ApiVersion: pulumi.String("networking.istio.io/v1beta1"),
			Kind:       pulumi.String("VirtualService"),
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(Namespace),
			},
			OtherFields: kubernetes.UntypedArgs{
				"spec": kubernetes.UntypedArgs{
					"gateways": []string{"istio-system/internal"},
					"hosts":    []string{"kibana.faust.dev"},
					"http": []kubernetes.UntypedArgs{{
						"match": []kubernetes.UntypedArgs{{
							"uri": kubernetes.UntypedArgs{
								"prefix": "/",
							},
						}},
						"route": []kubernetes.UntypedArgs{{
							"destination": kubernetes.UntypedArgs{
								"port": kubernetes.UntypedArgs{
									"number": 5601,
								},
								"host": "kibana-kibana",
							},
						}},
					}},
				},
			},
		},
	)
	return err
}

func NewFluentBit(ctx *pulumi.Context, bootstrap pulumi.Resource) error {
	const (
		ResourceName = "fluent-bit"
		Repository   = "https://fluent.github.io/helm-charts/"
		Chart        = "fluent-bit"
		ChartVersion = "0.19.15"

		Namespace = "monitoring"
	)

	inputs := `
[INPUT]
    Name tail
    Path /var/log/containers/*.log
    Parser cri
    Tag kube.*
    Mem_Buf_Limit 5MB
    Skip_Long_Lines On

[INPUT]
    Name systemd
    Tag host.*
    Systemd_Filter _SYSTEMD_UNIT=kubelet.service
    Read_From_Tail On
`

	outputs := `
[OUTPUT]
    Name es
    Match kube.*
    Host elasticsearch-master-headless
    Index kubernetes
    Logstash_Format On
    Logstash_Prefix kubernetes
    Retry_Limit False
    Replace_Dots On
    Trace_Error On

[OUTPUT]
    Name es
    Match host.*
    Host elasticsearch-master-headless
    Logstash_Format On
    Logstash_Prefix node
    Retry_Limit False
    Trace_Error On
`

	_, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: pulumi.Map{
			"hostNetwork": pulumi.Bool(true),
			"dnsPolicy":   pulumi.String("ClusterFirstWithHostNet"),
			"config": pulumi.Map{
				"inputs":  pulumi.String(inputs),
				"outputs": pulumi.String(outputs),
			},
		},
	}, pulumi.DependsOn([]pulumi.Resource{bootstrap}))
	return err
}
