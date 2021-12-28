package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/apiextensions"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func DeployEFKStack(ctx *pulumi.Context) error {
	if err := NewElasticsearch(ctx); err != nil {
		return nil
	}
	if err := NewKibana(ctx); err != nil {
		return err
	}
	if err := NewFluentBit(ctx); err != nil {
		return err
	}
	return nil
}

func NewElasticsearch(ctx *pulumi.Context) error {
	const (
		ResourceName = "elasticsearch"
		Repository   = "https://helm.elastic.co/"
		Chart        = "elasticsearch"
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
			"replicas":           pulumi.Int(1),
			"minimumMasterNodes": pulumi.Int(1),

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
	return err
}

func NewKibana(ctx *pulumi.Context) error {
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
	})
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

func NewFluentBit(ctx *pulumi.Context) error {
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
`

	outputs := `
[OUTPUT]
    Name es
    Match kube.*
    Host elasticsearch-master-headless
    Index kubernetes
    Logstash_Format On
    Retry_Limit False
    Replace_Dots On
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
	})
	return err
}
