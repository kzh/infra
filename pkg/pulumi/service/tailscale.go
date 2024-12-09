package service

import (
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/meta/v1"
	netv1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/networking/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

type TailscaleIngressArgs struct {
	Name      pulumi.String
	Namespace pulumi.String

	Hostname pulumi.String
	Service  pulumi.String
	Port     pulumi.Int
}

func NewTailscaleIngress(ctx *pulumi.Context, resource string, args *TailscaleIngressArgs) (*netv1.Ingress, error) {
	return netv1.NewIngress(ctx, resource, &netv1.IngressArgs{
		Metadata: &metav1.ObjectMetaArgs{
			Name:      args.Name,
			Namespace: args.Namespace,
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
										Name: args.Service,
										Port: &netv1.ServiceBackendPortArgs{
											Number: args.Port,
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
					Hosts: pulumi.StringArray{args.Hostname},
				},
			},
		},
	})
}
