from fastapi import APIRouter
from app.services.auth import login, require_user
from app.repos.user_repo import UserRepo

router = APIRouter()
users = UserRepo()


@router.post("/login")
def post_login(payload: dict):
    return {"token": login(payload["email"], payload["password"])}


@router.get("/me")
def me(token: str):
    user = require_user(token)
    return users.get(user.id)
