package service

import (
	"errors"

	"github.com/example/goshop/internal/repos"
)

type Pricing struct{}

func (Pricing) Line(p *repos.Product, qty int) repos.Line {
	return repos.Line{SKU: p.SKU, Qty: qty, Price: p.Price * float64(qty)}
}

func validate(items []Item) error {
	if len(items) == 0 {
		return errors.New("empty order")
	}
	for _, it := range items {
		if it.Qty <= 0 {
			return errors.New("bad qty")
		}
	}
	return nil
}

func total(lines []repos.Line) float64 {
	sum := 0.0
	for _, l := range lines {
		sum += l.Price
	}
	return withTax(sum)
}

func withTax(amount float64) float64 {
	return amount * 1.11
}
