package k8s

import (
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/util/homedir"
	"path/filepath"
)

func Config() (*rest.Config, error) {
	home := homedir.HomeDir()
	path := filepath.Join(home, ".kube", "config")

	return clientcmd.BuildConfigFromFlags("", path)
}

func Clientset() (*kubernetes.Clientset, error) {
	config, err := Config()
	if err != nil {
		return nil, err
	}

	return kubernetes.NewForConfig(config)
}

func CA() []byte {
	config, err := Config()
	if err != nil {
		return nil
	}
	return config.CAData
}
