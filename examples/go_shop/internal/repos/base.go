package repos

import (
	"context"
	"database/sql"

	"github.com/example/goshop/internal/db"
)

// BaseRepo is embedded by every repo: shared lookups by id.
type BaseRepo struct {
	conn  *db.Conn
	table string
}

func (r *BaseRepo) Find(ctx context.Context, id any) *sql.Row {
	return r.conn.QueryRow(ctx, "select * from "+r.table+" where id = $1", id)
}
