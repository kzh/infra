package vault

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/1Password/connect-sdk-go/connect"
	"github.com/1Password/connect-sdk-go/onepassword"
	"github.com/cockroachdb/errors"
	vault "github.com/hashicorp/vault/api"
	"github.com/kzh/infra-faust/pkg/k8s"
)

func NewVaultClient() (*vault.Client, error) {
	tmp, err := os.CreateTemp("", "*")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())

	_, err = tmp.Write(k8s.CA())
	if err != nil {
		return nil, err
	}

	config := vault.DefaultConfig()
	err = config.ConfigureTLS(&vault.TLSConfig{
		CACert: tmp.Name(),
	})
	if err != nil {
		return nil, err
	}

	return vault.NewClient(config)
}

func SaveVaultCredentials(init *vault.InitResponse) error {
	opc, err := NewOPClient()
	if err != nil {
		return err
	}

	vaults, err := opc.GetVaultsByTitle("mx")
	if err != nil {
		return err
	}
	if len(vaults) == 0 {
		return errors.New("missing onepassword vault")
	}
	opvault := vaults[0]

	fields := make([]*onepassword.ItemField, 0, len(init.Keys)+1)
	fields = append(fields, &onepassword.ItemField{
		Type:  "CONCEALED",
		Label: "Root Token",
		Value: init.RootToken,
	})
	for i, key := range init.Keys {
		fields = append(fields, &onepassword.ItemField{
			Type:  "CONCEALED",
			Label: "Unseal Key " + strconv.Itoa(i),
			Value: key,
		})
	}

	item := &onepassword.Item{
		Title:    "Vault Credentials",
		Category: onepassword.SecureNote,
		Vault: onepassword.ItemVault{
			ID: opvault.ID,
		},
		Fields: fields,
	}

	ret, err := opc.GetItemByTitle("Vault Credentials", opvault.ID)
	if err == nil {
		item.ID = ret.ID
		_, err = opc.UpdateItem(item, opvault.ID)
		return err
	} else {
		if !strings.Contains(err.Error(), "Found 0 item(s)") {
			return err
		}
		_, err = opc.CreateItem(item, opvault.ID)
		fmt.Println(err)
		return err
	}
}

func FetchVaultCredentials() (*vault.InitResponse, error) {
	opc, err := NewOPClient()
	if err != nil {
		return nil, err
	}

	vaults, err := opc.GetVaultsByTitle("mx")
	if err != nil {
		return nil, err
	}
	if len(vaults) == 0 {
		return nil, errors.New("missing onepassword vault")
	}
	opvault := vaults[0]

	item, err := opc.GetItemByTitle("Vault Credentials", opvault.ID)
	if err != nil {
		return nil, err
	}

	resp := &vault.InitResponse{
		RootToken: item.GetValue("Root Token"),
	}
	for _, field := range item.Fields[2:] {
		resp.Keys = append(resp.Keys, field.Value)
	}

	return resp, nil
}

func NewOPClient() (connect.Client, error) {
	return connect.NewClientFromEnvironment()
}
