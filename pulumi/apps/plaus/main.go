package main

import (
	"encoding/base64"
	"fmt"
	"github.com/kzh/infra-faust/pkg/pulumi/service"
	appsv1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/apps/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi-random/sdk/v4/go/random"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi/config"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		cfg := config.New(ctx, "")

		namespace, err := corev1.NewNamespace(ctx, "namespace", &corev1.NamespaceArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name: pulumi.String("plaus"),
			},
		})
		if err != nil {
			return err
		}

		clickhouse, err := helm.NewRelease(ctx, "clickhouse", &helm.ReleaseArgs{
			Chart:     pulumi.String("oci://registry-1.docker.io/bitnamicharts/clickhouse"),
			Namespace: namespace.Metadata.Name(),
			Version:   pulumi.String("7.0.2"),
			Values: pulumi.Map{
				"auth": pulumi.Map{
					"username": pulumi.String("admin"),
				},
				"zookeeper": pulumi.Map{
					"enabled": pulumi.Bool(false),
				},
				"ingress": pulumi.Map{
					"enabled": pulumi.Bool(false),
				},
				"resourcesPreset": pulumi.String("none"),
				"shards":          pulumi.Int(1),
				"replicaCount":    pulumi.Int(1),
			},
		})
		if err != nil {
			return err
		}

		postgres, err := helm.NewRelease(ctx, "postgres", &helm.ReleaseArgs{
			Chart:     pulumi.String("oci://registry-1.docker.io/bitnamicharts/postgresql"),
			Namespace: namespace.Metadata.Name(),
			Version:   pulumi.String("16.2.5"),
			Values: pulumi.Map{
				"auth": pulumi.Map{
					"database": pulumi.String("plausible_db"),
				},
			},
		})
		if err != nil {
			return err
		}

		databaseURL := pulumi.All(namespace.Metadata.Name().Elem(), postgres.Status.Name().Elem()).ApplyT(
			func(args []interface{}) (pulumi.StringOutput, error) {
				secret, err := corev1.GetSecret(ctx, "postgres-secret", pulumi.ID(fmt.Sprintf("%s/%s-postgresql", args[0], args[1])), nil)
				if err != nil {
					return pulumi.StringOutput{}, err
				}

				return secret.Data.MapIndex(pulumi.String("postgres-password")).ApplyT(func(data string) (string, error) {
					password, err := base64.StdEncoding.DecodeString(data)
					return fmt.Sprintf(
						"postgres://postgres:%s@%s-postgresql:5432/plausible_db",
						string(password),
						args[1],
					), err
				}).(pulumi.StringOutput), nil
			},
		).(pulumi.StringOutput)

		clickhouseURL := pulumi.All(namespace.Metadata.Name().Elem(), clickhouse.Status.Name().Elem()).ApplyT(
			func(args []interface{}) (pulumi.StringOutput, error) {
				secret, err := corev1.GetSecret(ctx, "clickhouse-secret", pulumi.ID(fmt.Sprintf("%s/%s", args[0], args[1])), nil)
				if err != nil {
					return pulumi.StringOutput{}, err
				}

				return secret.Data.MapIndex(pulumi.String("admin-password")).ApplyT(func(data string) (string, error) {
					password, err := base64.StdEncoding.DecodeString(data)
					return fmt.Sprintf(
						"http://admin:%s@%s:8123/default",
						string(password),
						args[1],
					), err
				}).(pulumi.StringOutput), nil
			},
		).(pulumi.StringOutput)

		secret, err := random.NewRandomPassword(ctx, "password", &random.RandomPasswordArgs{
			Length:  pulumi.Int(64),
			Special: pulumi.Bool(true),
			Lower:   pulumi.Bool(true),
			Upper:   pulumi.Bool(true),
			Numeric: pulumi.Bool(true),
		})

		plausibleSecret, err := corev1.NewSecret(ctx, "plausible-secret", &corev1.SecretArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("plausible"),
				Namespace: namespace.Metadata.Name(),
			},
			StringData: pulumi.StringMap{
				"DATABASE_URL":            databaseURL,
				"CLICKHOUSE_DATABASE_URL": clickhouseURL,
				"SECRET_KEY_BASE":         secret.Result,
			},
		})

		labels := pulumi.StringMap{
			"app": pulumi.String("plausible"),
		}

		_, err = appsv1.NewDeployment(ctx, "plausible", &appsv1.DeploymentArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("plausible"),
				Namespace: namespace.Metadata.Name(),
				Labels:    labels,
			},
			Spec: &appsv1.DeploymentSpecArgs{
				Selector: &metav1.LabelSelectorArgs{
					MatchLabels: labels,
				},
				Template: &corev1.PodTemplateSpecArgs{
					Metadata: &metav1.ObjectMetaArgs{
						Labels: labels,
					},
					Spec: &corev1.PodSpecArgs{
						Containers: corev1.ContainerArray{
							&corev1.ContainerArgs{
								Name:  pulumi.String("analytics"),
								Image: pulumi.String("plausible/analytics:v2"),
								Ports: corev1.ContainerPortArray{
									&corev1.ContainerPortArgs{
										ContainerPort: pulumi.Int(8000),
									},
								},
								Command: pulumi.StringArray{
									pulumi.String("sh"),
									pulumi.String("-c"),
									pulumi.String("sleep 10 && /entrypoint.sh db createdb && /entrypoint.sh db migrate && /entrypoint.sh run"),
								},
								Env: corev1.EnvVarArray{
									&corev1.EnvVarArgs{
										Name:  pulumi.String("BASE_URL"),
										Value: pulumi.Sprintf("https://%s", cfg.Require("BASE_URL")),
									},
								},
								EnvFrom: corev1.EnvFromSourceArray{
									&corev1.EnvFromSourceArgs{
										SecretRef: &corev1.SecretEnvSourceArgs{
											Name: plausibleSecret.Metadata.Name(),
										},
									},
								},
							},
						},
					},
				},
			},
		})
		if err != nil {
			return err
		}

		svc, err := corev1.NewService(ctx, "service", &corev1.ServiceArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Name:      pulumi.String("plausible"),
				Namespace: namespace.Metadata.Name(),
				Labels:    labels,
			},
			Spec: &corev1.ServiceSpecArgs{
				Type: pulumi.String("ClusterIP"),
				Ports: corev1.ServicePortArray{
					&corev1.ServicePortArgs{
						Port:       pulumi.Int(8000),
						TargetPort: pulumi.Int(8000),
					},
				},
				Selector: labels,
			},
		})
		if err != nil {
			return err
		}

		_, err = service.NewCloudflared(ctx, "plaus", &service.CloudflareArgs{
			Namespace: namespace.Metadata.Name().Elem(),
			Hostname:  pulumi.String("plaus.mxph.dev"),
			Service:   svc.Metadata.Name().Elem(),
			Port:      pulumi.Int(8000),
		})

		return nil
	})
}
