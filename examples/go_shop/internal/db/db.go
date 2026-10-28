package db

import (
	"context"
	"database/sql"
	"log"
)

// Conn wraps database/sql so every query goes through one place.
type Conn struct {
	sql *sql.DB
}

func Open(dsn string) *Conn {
	d, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatal(err)
	}
	return &Conn{sql: d}
}

func (c *Conn) QueryRow(ctx context.Context, query string, args ...any) *sql.Row {
	return c.sql.QueryRowContext(ctx, query, args...)
}

func (c *Conn) Exec(ctx context.Context, query string, args ...any) error {
	_, err := c.sql.ExecContext(ctx, query, args...)
	return err
}

func (c *Conn) Ping(ctx context.Context) error {
	return c.sql.PingContext(ctx)
}
