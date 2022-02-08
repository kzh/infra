package services

import (
	appsv1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/apps/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	rbacv1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/rbac/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi/config"
)

func NewTailscaleProxy(ctx *pulumi.Context, service *corev1.Service) {
	const (
		Tailscale = "tailscale"

		Image = "ghcr.io/kzh/tailscale-k8s:latest"
	)

	cfg := config.New(ctx, "")

	pulumi.All(service.Metadata, service.Spec, service.Status).ApplyT(func(args []interface{}) error {
		metadata := args[0].(*metav1.ObjectMeta)
		spec := args[1].(*corev1.ServiceSpec)

		service, namespace := *metadata.Name, *metadata.Namespace
		clusterIP := *spec.ClusterIP

		ResourceName := Tailscale + "-" + service

		// Secret
		_, err := corev1.NewSecret(ctx, ResourceName, &corev1.SecretArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(namespace),
			},
			StringData: pulumi.StringMap{
				"AUTH_KEY": cfg.RequireSecret("TS_AUTH_KEY"),
			},
		})

		// Role
		_, err = rbacv1.NewRole(ctx, ResourceName, &rbacv1.RoleArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(namespace),
			},
			Rules: rbacv1.PolicyRuleArray{
				rbacv1.PolicyRuleArgs{
					ApiGroups: pulumi.StringArray{
						pulumi.String(""),
					},
					Resources: pulumi.StringArray{
						pulumi.String("secrets"),
					},
					Verbs: pulumi.StringArray{
						pulumi.String("create"),
					},
				},
				rbacv1.PolicyRuleArgs{
					ApiGroups: pulumi.StringArray{
						pulumi.String(""),
					},
					Resources: pulumi.StringArray{
						pulumi.String("secrets"),
					},
					ResourceNames: pulumi.StringArray{
						pulumi.String(ResourceName),
					},
					Verbs: pulumi.StringArray{
						pulumi.String("get"),
						pulumi.String("update"),
					},
				},
			},
		})

		// ServiceAccount
		_, err = corev1.NewServiceAccount(ctx, ResourceName, &corev1.ServiceAccountArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(namespace),
			},
		})

		// RoleBinding
		_, err = rbacv1.NewRoleBinding(ctx, ResourceName, &rbacv1.RoleBindingArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(namespace),
			},
			Subjects: rbacv1.SubjectArray{
				rbacv1.SubjectArgs{
					Kind: pulumi.String("ServiceAccount"),
					Name: pulumi.String(ResourceName),
				},
			},
			RoleRef: rbacv1.RoleRefArgs{
				Kind:     pulumi.String("Role"),
				Name:     pulumi.String(ResourceName),
				ApiGroup: pulumi.String("rbac.authorization.k8s.io"),
			},
		})

		// Deployment
		_, err = appsv1.NewDeployment(ctx, ResourceName, &appsv1.DeploymentArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(namespace),
			},
			Spec: &appsv1.DeploymentSpecArgs{
				Selector: metav1.LabelSelectorArgs{
					MatchLabels: pulumi.StringMap{
						"app.kubernetes.io/name": pulumi.String(ResourceName),
					},
				},
				Template: corev1.PodTemplateSpecArgs{
					Metadata: metav1.ObjectMetaArgs{
						Name:      pulumi.String(ResourceName),
						Namespace: pulumi.String(namespace),
						Labels: pulumi.StringMap{
							"app.kubernetes.io/name": pulumi.String(ResourceName),
						},
					},
					Spec: corev1.PodSpecArgs{
						ServiceAccountName: pulumi.String(ResourceName),
						InitContainers: corev1.ContainerArray{
							corev1.ContainerArgs{
								Name:  pulumi.String("sysctler"),
								Image: pulumi.String("busybox"),
								SecurityContext: corev1.SecurityContextArgs{
									Privileged: pulumi.Bool(true),
								},
								Command: pulumi.StringArray{
									pulumi.String("/bin/sh"),
								},
								Args: pulumi.StringArray{
									pulumi.String("-c"),
									pulumi.String("sysctl -w net.ipv4.ip_forward=1"),
								},
							},
						},
						Containers: corev1.ContainerArray{
							corev1.ContainerArgs{
								Name:  pulumi.String(Tailscale),
								Image: pulumi.String(Image),
								SecurityContext: corev1.SecurityContextArgs{
									Capabilities: corev1.CapabilitiesArgs{
										Add: pulumi.StringArray{
											pulumi.String("NET_ADMIN"),
										},
									},
								},
								Env: corev1.EnvVarArray{
									corev1.EnvVarArgs{
										Name:  pulumi.String("KUBE_SECRET"),
										Value: pulumi.String(ResourceName),
									},
									corev1.EnvVarArgs{
										Name:  pulumi.String("USERSPACE"),
										Value: pulumi.String("false"),
									},
									corev1.EnvVarArgs{
										Name:  pulumi.String("DEST_IP"),
										Value: pulumi.String(clusterIP),
									},
									corev1.EnvVarArgs{
										Name:  pulumi.String("EXTRA_ARGS"),
										Value: pulumi.String("--hostname=" + service),
									},
									corev1.EnvVarArgs{
										Name: pulumi.String("AUTH_KEY"),
										ValueFrom: corev1.EnvVarSourceArgs{
											SecretKeyRef: corev1.SecretKeySelectorArgs{
												Name: pulumi.String(ResourceName),
												Key:  pulumi.String("AUTH_KEY"),
											},
										},
									},
								},
							},
						},
					},
				},
			},
		})
		return err
	})
}
