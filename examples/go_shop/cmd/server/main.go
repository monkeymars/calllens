// HTTP server: Go 1.22 net/http routing with method patterns.
package main

import (
	"log"
	"net/http"

	"github.com/example/goshop/internal/db"
	"github.com/example/goshop/internal/handlers"
	"github.com/example/goshop/internal/service"
)

func main() {
	conn := db.Open("postgres://localhost/shop")
	svc := service.NewOrderService(conn)
	h := handlers.NewOrderHandler(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /orders", h.Create)
	mux.HandleFunc("GET /orders/{id}", h.Get)
	mux.HandleFunc("POST /orders/{id}/cancel", h.Cancel)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := conn.Ping(r.Context()); err != nil {
			http.Error(w, "db down", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	log.Fatal(http.ListenAndServe(":8080", mux))
}
