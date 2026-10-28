package repos

import (
	"context"

	"github.com/example/goshop/internal/db"
)

type Product struct {
	SKU   string
	Price float64
	Stock int
}

type ProductRepo struct {
	BaseRepo
}

func NewProductRepo(conn *db.Conn) *ProductRepo {
	return &ProductRepo{BaseRepo{conn: conn, table: "products"}}
}

func (r *ProductRepo) Get(ctx context.Context, sku string) (*Product, error) {
	var p Product
	err := r.Find(ctx, sku).Scan(&p.SKU, &p.Price, &p.Stock)
	return &p, err
}

func (r *ProductRepo) DecrementStock(ctx context.Context, sku string, qty int) error {
	return r.conn.Exec(ctx, "update products set stock = stock - $1 where sku = $2", qty, sku)
}

func (r *ProductRepo) Restock(ctx context.Context, sku string, qty int) error {
	return r.conn.Exec(ctx, "update products set stock = stock + $1 where sku = $2", qty, sku)
}
