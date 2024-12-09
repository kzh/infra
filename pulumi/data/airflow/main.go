package main

import (
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/meta/v1"
	netv1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/networking/v1"
	"github.com/pulumi/pulumi-random/sdk/v4/go/random"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		namespace, err := corev1.NewNamespace(ctx, "namespace", &corev1.NamespaceArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name: pulumi.String("airflow"),
			},
		})
		if err != nil {
			return err
		}

		secret, err := random.NewRandomPassword(ctx, "password", &random.RandomPasswordArgs{
			Length:  pulumi.Int(16),
			Special: pulumi.Bool(true),
			Lower:   pulumi.Bool(true),
			Upper:   pulumi.Bool(true),
			Numeric: pulumi.Bool(true),
		})

		chart, err := helm.NewRelease(ctx, "airflow", &helm.ReleaseArgs{
			Chart: pulumi.String("airflow"),
			RepositoryOpts: &helm.RepositoryOptsArgs{
				Repo: pulumi.String("https://airflow.apache.org"),
			},
			Values: pulumi.Map{
				"createUserJob": pulumi.Map{
					"useHelmHooks":   pulumi.Bool(false),
					"applyCustomEnv": pulumi.Bool(false),
				},
				"migrateDatabaseJob": pulumi.Map{
					"useHelmHooks":   pulumi.Bool(false),
					"applyCustomEnv": pulumi.Bool(false),
				},
				"webserverSecretKey": secret,
			},
			Namespace: namespace.Metadata.Name(),
		})
		if err != nil {
			return err
		}

		_, err = netv1.NewIngress(ctx, "ingress", &netv1.IngressArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("airflow"),
				Namespace: namespace.Metadata.Name(),
			},
			Spec: &netv1.IngressSpecArgs{
				IngressClassName: pulumi.String("tailscale"),
				Rules: &netv1.IngressRuleArray{
					&netv1.IngressRuleArgs{
						Http: &netv1.HTTPIngressRuleValueArgs{
							Paths: &netv1.HTTPIngressPathArray{
								&netv1.HTTPIngressPathArgs{
									Path:     pulumi.String("/"),
									PathType: pulumi.String("Prefix"),
									Backend: &netv1.IngressBackendArgs{
										Service: &netv1.IngressServiceBackendArgs{
											Name: pulumi.Sprintf("%s-webserver", chart.Name.Elem()),
											Port: &netv1.ServiceBackendPortArgs{
												Number: pulumi.Int(8080),
											},
										},
									},
								},
							},
						},
					},
				},
				Tls: &netv1.IngressTLSArray{
					&netv1.IngressTLSArgs{
						Hosts: pulumi.StringArray{pulumi.String("airflow")},
					},
				},
			},
		})

		return nil
	})
}
