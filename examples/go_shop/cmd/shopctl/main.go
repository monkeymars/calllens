// Admin CLI built with cobra.
package main

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/example/goshop/internal/db"
	"github.com/example/goshop/internal/repos"
)

func main() {
	root := &cobra.Command{Use: "shopctl"}
	root.AddCommand(&cobra.Command{
		Use:   "restock <sku>",
		Short: "Add stock for a product",
		RunE: func(cmd *cobra.Command, args []string) error {
			conn := db.Open(os.Getenv("DATABASE_URL"))
			products := repos.NewProductRepo(conn)
			return products.Restock(cmd.Context(), args[0], 10)
		},
	})
	root.AddCommand(&cobra.Command{Use: "migrate", Short: "Create tables", RunE: runMigrate})
	if err := root.Execute(); err != nil {
		os.Exit(1)
	}
}

func runMigrate(cmd *cobra.Command, args []string) error {
	conn := db.Open(os.Getenv("DATABASE_URL"))
	return conn.Exec(cmd.Context(), "create table if not exists orders (id serial primary key)")
}
