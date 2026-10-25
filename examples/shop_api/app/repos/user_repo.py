from app.repos.base import BaseRepo
from app.infra import db


class UserRepo(BaseRepo):
    table = "users"

    def find_by_email(self, email):
        return db.query_one("select * from users where email=?", email)
