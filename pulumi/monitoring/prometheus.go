package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/apiextensions"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func DeployPrometheusStack(ctx *pulumi.Context) error {
	const (
		ResourceName = "kube-prometheus-stack"
		Repository   = "https://prometheus-community.github.io/helm-charts"
		Chart        = "kube-prometheus-stack"
		ChartVersion = "27.0.1"

		Namespace = "monitoring"
	)

	values := pulumi.Map{
		"prometheus": pulumi.Map{
			"prometheusSpec": pulumi.Map{
				"storageSpec": pulumi.Map{
					"volumeClaimTemplate": pulumi.Map{
						"spec": pulumi.Map{
							"storageClassName": pulumi.String("rook-ceph-block"),
							"accessModes":      pulumi.StringArray{pulumi.String("ReadWriteOnce")},
							"resources": pulumi.Map{
								"requests": pulumi.Map{
									"storage": pulumi.String("20Gi"),
								},
							},
						},
					},
				},
			},
		},
		"grafana": pulumi.Map{
			"persistence": pulumi.Map{
				"enabled": pulumi.Bool(true),
			},
		},
	}

	_, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: values,
	})
	if err != nil {
		return err
	}

	_, err = apiextensions.NewCustomResource(ctx,
		"grafana",
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
					"hosts":    []string{"grafana.faust.dev"},
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
								"host": "kube-prometheus-stack-grafana",
							},
						}},
					}},
				},
			},
		},
	)
	if err != nil {
		return nil
	}

	_, err = apiextensions.NewCustomResource(ctx,
		"prometheus",
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
					"hosts":    []string{"prometheus.faust.dev"},
					"http": []kubernetes.UntypedArgs{{
						"match": []kubernetes.UntypedArgs{{
							"uri": kubernetes.UntypedArgs{
								"prefix": "/",
							},
						}},
						"route": []kubernetes.UntypedArgs{{
							"destination": kubernetes.UntypedArgs{
								"port": kubernetes.UntypedArgs{
									"number": 9090,
								},
								"host": "kube-prometheus-stack-prometheus",
							},
						}},
					}},
				},
			},
		},
	)
	return err
}
