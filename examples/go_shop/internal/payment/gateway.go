package payment

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"
)

var ErrDeclined = errors.New("payment declined")

type Gateway struct {
	baseURL string
	client  *http.Client
}

func NewGateway(baseURL string) *Gateway {
	return &Gateway{baseURL: baseURL, client: &http.Client{Timeout: 5 * time.Second}}
}

func (g *Gateway) Charge(ctx context.Context, userID int64, amount float64) error {
	body, _ := json.Marshal(map[string]any{"user": userID, "amount": amount})
	resp, err := g.client.Post(g.baseURL+"/charge", "application/json", bytes.NewReader(body))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return ErrDeclined
	}
	return nil
}

func (g *Gateway) Refund(ctx context.Context, ref string) error {
	body, _ := json.Marshal(map[string]string{"ref": ref})
	resp, err := g.client.Post(g.baseURL+"/refund", "application/json", bytes.NewReader(body))
	if err != nil {
		return err
	}
	return resp.Body.Close()
}
