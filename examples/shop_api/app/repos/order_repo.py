from app.repos.base import BaseRepo
from app.infra import db


class OrderRepo(BaseRepo):
    table = "orders"

    def create(self, user_id, lines, total):
        order_id = self._insert({"user_id": user_id, "total": total})
        for line in lines:
            db.execute("insert into order_lines", {**line, "order_id": order_id})
        return self.get(order_id)

    def update_status(self, order_id, status):
        db.execute("update orders set status=? where id=?", status, order_id)

    def mark_failed(self, order_id):
        self.update_status(order_id, "failed")
