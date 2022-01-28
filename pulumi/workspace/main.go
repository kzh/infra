package main

import (
	appsv1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/apps/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	rbacv1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/rbac/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi/config"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		const (
			ResourceName = "workspace"
			Namespace    = "workspace"

			Image   = "ghcr.io/kzh/workspace:1.1"
			TSImage = "ghcr.io/kzh/tailscale-k8s:latest"

			Volume = "40Gi"
		)

		cfg := config.New(ctx, "")

		namespace, err := corev1.NewNamespace(ctx, ResourceName, &corev1.NamespaceArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(Namespace),
			},
		})
		if err != nil {
			return err
		}

		pvc, err := corev1.NewPersistentVolumeClaim(ctx, ResourceName, &corev1.PersistentVolumeClaimArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(Namespace),
			},
			Spec: corev1.PersistentVolumeClaimSpecArgs{
				AccessModes: pulumi.StringArray{
					pulumi.String("ReadWriteMany"),
				},
				Resources: corev1.ResourceRequirementsArgs{
					Requests: pulumi.StringMap{
						"storage": pulumi.String(Volume),
					},
				},
				StorageClassName: pulumi.StringPtr("rook-cephfs"),
			},
		}, pulumi.DependsOn([]pulumi.Resource{namespace}))
		if err != nil {
			return err
		}

		_, err = corev1.NewSecret(ctx, "tailscale", &corev1.SecretArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String("tailscale"),
				Namespace: pulumi.String(Namespace),
			},
			StringData: pulumi.StringMap{
				"AUTH_KEY": cfg.RequireSecret("TS_AUTH_KEY"),
			},
		})

		_, err = rbacv1.NewRole(ctx, "tailscale", &rbacv1.RoleArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("tailscale"),
				Namespace: pulumi.String(Namespace),
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
						pulumi.String("tailscale"),
					},
					Verbs: pulumi.StringArray{
						pulumi.String("get"),
						pulumi.String("update"),
					},
				},
			},
		})

		_, err = corev1.NewServiceAccount(ctx, "tailscale", &corev1.ServiceAccountArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("tailscale"),
				Namespace: pulumi.String(Namespace),
			},
		})

		_, err = rbacv1.NewRoleBinding(ctx, "tailscale", &rbacv1.RoleBindingArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("tailscale"),
				Namespace: pulumi.String(Namespace),
			},
			Subjects: rbacv1.SubjectArray{
				rbacv1.SubjectArgs{
					Kind: pulumi.String("ServiceAccount"),
					Name: pulumi.String("tailscale"),
				},
			},
			RoleRef: rbacv1.RoleRefArgs{
				Kind:     pulumi.String("Role"),
				Name:     pulumi.String("tailscale"),
				ApiGroup: pulumi.String("rbac.authorization.k8s.io"),
			},
		})

		_, err = appsv1.NewDeployment(ctx, ResourceName, &appsv1.DeploymentArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(Namespace),
			},
			Spec: appsv1.DeploymentSpecArgs{
				Selector: metav1.LabelSelectorArgs{
					MatchLabels: pulumi.StringMap{
						"app.kubernetes.io/name": pulumi.String("ubuntu"),
					},
				},
				Template: corev1.PodTemplateSpecArgs{
					Metadata: &metav1.ObjectMetaArgs{
						Name:      pulumi.String(ResourceName),
						Namespace: pulumi.String(Namespace),
						Labels: pulumi.StringMap{
							"app.kubernetes.io/name": pulumi.String("ubuntu"),
						},
					},
					Spec: corev1.PodSpecArgs{
						Hostname:           pulumi.String(ResourceName),
						ServiceAccountName: pulumi.String("tailscale"),
						Containers: corev1.ContainerArray{
							corev1.ContainerArgs{
								Name:  pulumi.String(ResourceName),
								Image: pulumi.String(Image),
								Command: pulumi.StringArray{
									pulumi.String("tail"),
								},
								Args: pulumi.StringArray{
									pulumi.String("-f"),
									pulumi.String("/dev/null"),
								},
								VolumeMounts: corev1.VolumeMountArray{
									corev1.VolumeMountArgs{
										MountPath: pulumi.String("/home/kevin"),
										Name:      pulumi.String(ResourceName),
									},
								},
							},
							corev1.ContainerArgs{
								Name:  pulumi.String("tailscale"),
								Image: pulumi.String(TSImage),
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
										Value: pulumi.String("tailscale"),
									},
									corev1.EnvVarArgs{
										Name:  pulumi.String("USERSPACE"),
										Value: pulumi.String("false"),
									},
									corev1.EnvVarArgs{
										Name: pulumi.String("AUTH_KEY"),
										ValueFrom: corev1.EnvVarSourceArgs{
											SecretKeyRef: corev1.SecretKeySelectorArgs{
												Name: pulumi.String("tailscale"),
												Key:  pulumi.String("AUTH_KEY"),
											},
										},
									},
								},
							},
						},
						Volumes: corev1.VolumeArray{
							corev1.VolumeArgs{
								Name: pulumi.String(ResourceName),
								PersistentVolumeClaim: corev1.PersistentVolumeClaimVolumeSourceArgs{
									ClaimName: pulumi.String(ResourceName),
								},
							},
						},
					},
				},
			},
		}, pulumi.DependsOn([]pulumi.Resource{pvc}))

		return nil
	})
}
