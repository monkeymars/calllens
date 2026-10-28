package repos

import (
	"context"

	"github.com/example/goshop/internal/db"
)

type Line struct {
	SKU   string
	Qty   int
	Price float64
}

type Order struct {
	ID         int64
	UserID     int64
	Total      float64
	PaymentRef string
}

type OrderRepo struct {
	BaseRepo
}

func NewOrderRepo(conn *db.Conn) *OrderRepo {
	return &OrderRepo{BaseRepo{conn: conn, table: "orders"}}
}

func (r *OrderRepo) Get(ctx context.Context, id int64) (*Order, error) {
	var o Order
	err := r.Find(ctx, id).Scan(&o.ID, &o.UserID, &o.Total, &o.PaymentRef)
	return &o, err
}

func (r *OrderRepo) Create(ctx context.Context, userID int64, lines []Line, total float64) (*Order, error) {
	o := &Order{UserID: userID, Total: total}
	if err := r.conn.QueryRow(ctx, "insert into orders (user_id, total) values ($1, $2) returning id", userID, total).Scan(&o.ID); err != nil {
		return nil, err
	}
	for _, l := range lines {
		if err := r.conn.Exec(ctx, "insert into order_lines values ($1, $2, $3)", o.ID, l.SKU, l.Qty); err != nil {
			return nil, err
		}
	}
	return o, nil
}

func (r *OrderRepo) UpdateStatus(ctx context.Context, id int64, status string) error {
	return r.conn.Exec(ctx, "update orders set status = $1 where id = $2", status, id)
}

func (r *OrderRepo) MarkFailed(ctx context.Context, id int64) error {
	return r.UpdateStatus(ctx, id, "failed")
}
