from app.repos.base import BaseRepo
from app.infra import db


class ProductRepo(BaseRepo):
    table = "products"

    def decrement_stock(self, sku, qty):
        db.execute("update products set stock = stock - ? where sku=?", qty, sku)
