from app.infra import db


class BaseRepo:
    table = ""

    def get(self, id):
        return db.query_one(f"select * from {self.table} where id=?", id)

    def _insert(self, row: dict):
        return db.execute(f"insert into {self.table}", row)
