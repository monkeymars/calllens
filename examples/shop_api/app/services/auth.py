from app.repos.user_repo import UserRepo
from app.infra import cache, crypto

repo = UserRepo()


def login(email: str, password: str) -> str:
    user = repo.find_by_email(email)
    if user is None or not crypto.verify(password, user.password_hash):
        raise ValueError("bad credentials")
    token = crypto.sign({"uid": user.id})
    cache.set(token, user.id, ttl=3600)
    return token


def require_user(token: str):
    uid = cache.get(token)
    if uid is None:
        uid = crypto.unsign(token)["uid"]
    return repo.get(uid)
