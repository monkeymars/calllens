from app.repos.discount_repo import DiscountRepo


class PricingEngine:
    def __init__(self):
        self.discounts = DiscountRepo()

    def price_line(self, product, qty: int, user):
        discount = self.discounts.best_for(user, product)
        unit = self._apply(product.price, discount)
        return {"sku": product.sku, "qty": qty, "unit": unit}

    def total(self, lines):
        subtotal = sum(l["unit"] * l["qty"] for l in lines)
        return self._with_tax(subtotal)

    def _apply(self, price, discount):
        return price * (1 - (discount or 0))

    def _with_tax(self, amount):
        return round(amount * 1.11, 2)
