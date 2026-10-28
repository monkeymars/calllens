package service

import (
	"context"
	"errors"

	"github.com/example/goshop/internal/auth"
	"github.com/example/goshop/internal/events"
	"github.com/example/goshop/internal/repos"
)

var ErrNotFound = errors.New("not found")

func (s *OrderService) Get(ctx context.Context, user *auth.User, id int64) (*repos.Order, error) {
	order, err := s.orders.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if order.UserID != user.ID {
		return nil, ErrNotFound
	}
	return order, nil
}

func (s *OrderService) Cancel(ctx context.Context, user *auth.User, id int64) error {
	order, err := s.Get(ctx, user, id)
	if err != nil {
		return err
	}
	if err := s.payments.Refund(ctx, order.PaymentRef); err != nil {
		return err
	}
	if err := s.orders.UpdateStatus(ctx, id, "cancelled"); err != nil {
		return err
	}
	events.Publish("order.cancelled", id)
	return nil
}
