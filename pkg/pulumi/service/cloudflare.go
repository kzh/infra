package service

import (
	"encoding/base64"
	"github.com/pulumi/pulumi-cloudflare/sdk/v5/go/cloudflare"
	appsv1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/apps/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/core/v1"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi-random/sdk/v4/go/random"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi/config"
)

type CloudflareArgs struct {
	Namespace pulumi.StringInput
	Hostname  pulumi.StringInput
	Service   pulumi.StringInput
	Port      pulumi.IntInput
}

func NewCloudflared(ctx *pulumi.Context, resource string, args *CloudflareArgs) (pulumi.Resource, error) {
	cfg := config.New(ctx, "")

	accountID := cfg.Require("CLOUDFLARE_ACCOUNT_ID")
	secret, err := random.NewRandomPassword(ctx, resource+"-tunnel-secret", &random.RandomPasswordArgs{
		Length:  pulumi.Int(32),
		Special: pulumi.Bool(true),
		Lower:   pulumi.Bool(true),
		Upper:   pulumi.Bool(true),
		Numeric: pulumi.Bool(true),
	})
	if err != nil {
		return nil, err
	}

	tunnel, err := cloudflare.NewZeroTrustTunnelCloudflared(ctx, resource+"-tunnel", &cloudflare.ZeroTrustTunnelCloudflaredArgs{
		Name:      pulumi.String(resource),
		AccountId: pulumi.String(accountID),
		Secret: secret.Result.ApplyT(func(str string) (string, error) {
			return base64.StdEncoding.EncodeToString([]byte(str)), nil
		}).(pulumi.StringOutput),
	})
	if err != nil {
		return nil, err
	}

	_, err = cloudflare.NewZeroTrustTunnelCloudflaredConfig(ctx, resource+"-tunnel-config", &cloudflare.ZeroTrustTunnelCloudflaredConfigArgs{
		AccountId: pulumi.String(accountID),
		TunnelId:  tunnel.ID(),
		Config: cloudflare.ZeroTrustTunnelCloudflaredConfigConfigArgs{
			IngressRules: cloudflare.ZeroTrustTunnelCloudflaredConfigConfigIngressRuleArray{
				cloudflare.ZeroTrustTunnelCloudflaredConfigConfigIngressRuleArgs{
					Hostname: pulumi.String(cfg.Require("BASE_URL")),
					Service:  pulumi.Sprintf("http://%s:%d", args.Service, args.Port),
				},
				cloudflare.ZeroTrustTunnelCloudflaredConfigConfigIngressRuleArgs{
					Service: pulumi.String("http_status:404"),
				},
			},
		},
	})
	if err != nil {
		return nil, err
	}

	_, err = cloudflare.NewRecord(ctx, resource+"-cname", &cloudflare.RecordArgs{
		ZoneId:  pulumi.String(cfg.Require("CLOUDFLARE_ZONE_ID")),
		Name:    args.Hostname,
		Type:    pulumi.String("CNAME"),
		Content: tunnel.Cname,
		Proxied: pulumi.Bool(true),
	})
	if err != nil {
		return nil, err
	}

	cloudflared, err := appsv1.NewDeployment(ctx, resource+"-deployment", &appsv1.DeploymentArgs{
		Metadata: &metav1.ObjectMetaArgs{
			Name:      pulumi.String(resource + "-cloudflared"),
			Namespace: args.Namespace,
		},
		Spec: appsv1.DeploymentSpecArgs{
			Selector: &metav1.LabelSelectorArgs{
				MatchLabels: pulumi.StringMap{
					"app": pulumi.String(resource + "-cloudflared"),
				},
			},
			Template: &corev1.PodTemplateSpecArgs{
				Metadata: &metav1.ObjectMetaArgs{
					Labels: pulumi.StringMap{
						"app": pulumi.String(resource + "-cloudflared"),
					},
				},
				Spec: corev1.PodSpecArgs{
					Containers: corev1.ContainerArray{
						corev1.ContainerArgs{
							Name:  pulumi.String("cloudflared"),
							Image: pulumi.String("cloudflare/cloudflared:latest"),
							Command: pulumi.StringArray{
								pulumi.String("cloudflared"),
								pulumi.String("tunnel"),
								pulumi.String("--metrics"),
								pulumi.String("0.0.0.0:2000"),
								pulumi.String("run"),
							},
							Args: pulumi.StringArray{
								pulumi.String("--token"),
								tunnel.TunnelToken,
							},
							LivenessProbe: corev1.ProbeArgs{
								HttpGet: &corev1.HTTPGetActionArgs{
									Path: pulumi.String("/ready"),
									Port: pulumi.Int(2000),
								},
								FailureThreshold:    pulumi.Int(1),
								InitialDelaySeconds: pulumi.Int(10),
								PeriodSeconds:       pulumi.Int(10),
							},
						},
					},
				},
			},
		},
	})

	return cloudflared, nil
}
