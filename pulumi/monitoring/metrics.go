package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/apiextensions"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func NewMetricsServer(ctx *pulumi.Context) error {
	const (
		ResourceName = "metrics-server"
		Repository   = "https://kubernetes-sigs.github.io/metrics-server/"
		Chart        = "metrics-server"
		ChartVersion = "3.7.0"

		Namespace = "kube-system"
	)

	_, err := helm.NewRelease(ctx, ResourceName, &helm.ReleaseArgs{
		Namespace: pulumi.String(Namespace),
		Name:      pulumi.String(Chart),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		RepositoryOpts: helm.RepositoryOptsArgs{
			Repo: pulumi.String(Repository),
		},
		Atomic: pulumi.Bool(true),
	})
	return err
}

func NewKubernetesDashboard(ctx *pulumi.Context) error {
	const (
		ResourceName = "kubernetes-dashboard"
		Repository   = "https://kubernetes.github.io/dashboard/"
		Chart        = "kubernetes-dashboard"
		ChartVersion = "5.0.5"

		Namespace = "monitoring"
	)

	_, err := helm.NewRelease(ctx, ResourceName,
		&helm.ReleaseArgs{
			CreateNamespace: pulumi.Bool(true),
			Namespace:       pulumi.String(Namespace),
			Name:            pulumi.String(Chart),
			Chart:           pulumi.String(Chart),
			Version:         pulumi.String(ChartVersion),
			RepositoryOpts: helm.RepositoryOptsArgs{
				Repo: pulumi.String(Repository),
			},
			Atomic: pulumi.Bool(true),
			Values: pulumi.Map{
				"rbac": pulumi.Map{
					"clusterReadOnlyRole": pulumi.Bool(true),
				},
				"service": pulumi.Map{
					"externalPort": pulumi.Int(80),
				},
				"protocolHttp": pulumi.Bool(true),
				"metricsScraper": pulumi.Map{
					"enabled": pulumi.Bool(true),
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
					"hosts":    []string{"k8s.faust.dev"},
					"http": []kubernetes.UntypedArgs{{
						"match": []kubernetes.UntypedArgs{{
							"uri": kubernetes.UntypedArgs{
								"prefix": "/",
							},
						}},
						"route": []kubernetes.UntypedArgs{{
							"destination": kubernetes.UntypedArgs{
								"port": kubernetes.UntypedArgs{
									"number": 80,
								},
								"host": "kubernetes-dashboard",
							},
						}},
					}},
				},
			},
		},
	)
	return err
}
