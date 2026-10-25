from app.repos.base import BaseRepo
from app.infra import db


class DiscountRepo(BaseRepo):
    table = "discounts"

    def best_for(self, user, product):
        rows = db.query("select * from discounts where sku=? or tier=?", product.sku, user.tier)
        return max((r.pct for r in rows), default=None)
