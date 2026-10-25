from app.repos.order_repo import OrderRepo
from app.repos.product_repo import ProductRepo
from app.services.pricing import PricingEngine
from app.infra.payment import PaymentGateway
from app.infra import events, log


class OrderService:
    def __init__(self):
        self.orders = OrderRepo()
        self.products = ProductRepo()
        self.pricing = PricingEngine()
        self.payments = PaymentGateway()

    def place(self, user, items: list):
        log.info("placing order")
        self._validate_items(items)
        lines = []
        for item in items:
            product = self.products.get(item["sku"])
            if product.stock < item["qty"]:
                raise ValueError("out of stock")
            lines.append(self.pricing.price_line(product, item["qty"], user))
        total = self.pricing.total(lines)
        order = self.orders.create(user.id, lines, total)
        try:
            self.payments.charge(user, total)
        except Exception:
            self.orders.mark_failed(order.id)
            raise
        for item in items:
            self.products.decrement_stock(item["sku"], item["qty"])
        events.publish("order.placed", order.id)
        return order

    def get(self, user, order_id: int):
        order = self.orders.get(order_id)
        if order and order.user_id != user.id:
            return None
        return order

    def cancel(self, user, order_id: int):
        order = self.get(user, order_id)
        if order is None:
            raise ValueError("not found")
        self.payments.refund(order.payment_ref)
        self.orders.update_status(order.id, "cancelled")
        events.publish("order.cancelled", order.id)
        return order

    def _validate_items(self, items):
        if not items:
            raise ValueError("empty order")
        for item in items:
            if item["qty"] <= 0:
                raise ValueError("bad qty")
