package infra

import (
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/util/homedir"
	"path/filepath"
)

func K8SConfig() (*rest.Config, error) {
	home := homedir.HomeDir()
	path := filepath.Join(home, ".kube", "config")

	return clientcmd.BuildConfigFromFlags("", path)
}

func K8SClientset() (*kubernetes.Clientset, error) {
	config, err := K8SConfig()
	if err != nil {
		return nil, err
	}

	return kubernetes.NewForConfig(config)
}

func K8SCA() []byte {
	config, err := K8SConfig()
	if err != nil {
		return nil
	}
	return config.CAData
}
