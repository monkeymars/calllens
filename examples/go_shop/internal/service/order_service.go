package service

import (
	"context"
	"errors"

	"github.com/example/goshop/internal/auth"
	"github.com/example/goshop/internal/db"
	"github.com/example/goshop/internal/events"
	"github.com/example/goshop/internal/payment"
	"github.com/example/goshop/internal/repos"
)

var ErrOutOfStock = errors.New("out of stock")

type Item struct {
	SKU string `json:"sku"`
	Qty int    `json:"qty"`
}

type OrderService struct {
	orders   *repos.OrderRepo
	products *repos.ProductRepo
	payments *payment.Gateway
	pricing  Pricing
}

func NewOrderService(conn *db.Conn) *OrderService {
	return &OrderService{
		orders:   repos.NewOrderRepo(conn),
		products: repos.NewProductRepo(conn),
		payments: payment.NewGateway("https://pay.example"),
	}
}

func (s *OrderService) Place(ctx context.Context, user *auth.User, items []Item) (*repos.Order, error) {
	if err := validate(items); err != nil {
		return nil, err
	}
	var lines []repos.Line
	for _, it := range items {
		p, err := s.products.Get(ctx, it.SKU)
		if err != nil {
			return nil, err
		}
		if p.Stock < it.Qty {
			return nil, ErrOutOfStock
		}
		lines = append(lines, s.pricing.Line(p, it.Qty))
	}
	order, err := s.orders.Create(ctx, user.ID, lines, total(lines))
	if err != nil {
		return nil, err
	}
	if err := s.payments.Charge(ctx, user.ID, order.Total); err != nil {
		s.orders.MarkFailed(ctx, order.ID)
		return nil, err
	}
	for _, it := range items {
		s.products.DecrementStock(ctx, it.SKU, it.Qty)
	}
	go events.Publish("order.placed", order.ID)
	return order, nil
}
