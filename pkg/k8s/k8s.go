package k8s

import (
	"context"
	"net/http"
	"path/filepath"
	"sort"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/portforward"
	"k8s.io/client-go/transport/spdy"
	"k8s.io/client-go/util/homedir"
	"k8s.io/kubectl/pkg/polymorphichelpers"
	"k8s.io/kubectl/pkg/util/podutils"
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

func PortForward(
	service, namespace string,
	ports []string,
	stop <-chan struct{},
	ready chan struct{},
) error {
	config, err := Config()
	if err != nil {
		return err
	}

	clientset, err := Clientset()
	if err != nil {
		return err
	}

	svc, err := clientset.CoreV1().Services(namespace).Get(context.Background(), service, metav1.GetOptions{})
	if err != nil {
		return err
	}

	namespace, selector, err := polymorphichelpers.SelectorsForObject(svc)
	if err != nil {
		return err
	}

	sortBy := func(pods []*corev1.Pod) sort.Interface { return sort.Reverse(podutils.ActivePods(pods)) }
	pod, _, err := polymorphichelpers.GetFirstPod(clientset.CoreV1(), namespace, selector.String(), time.Second*30, sortBy)
	if err != nil {
		return err
	}

	req := clientset.CoreV1().RESTClient().Post().
		Resource("pods").
		Name(pod.Name).
		Namespace(namespace).
		SubResource("portforward")

	transport, upgrader, err := spdy.RoundTripperFor(config)
	if err != nil {
		return err
	}

	dialer := spdy.NewDialer(upgrader, &http.Client{Transport: transport}, "POST", req.URL())
	fw, err := portforward.New(dialer, ports, stop, ready, nil, nil)
	if err != nil {
		return err
	}

	return fw.ForwardPorts()
}
