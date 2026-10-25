from fastapi import APIRouter, HTTPException
from app.services.order_service import OrderService
from app.services.auth import require_user

router = APIRouter()
svc = OrderService()


@router.post("/orders")
def create_order(payload: dict, token: str):
    user = require_user(token)
    try:
        order = svc.place(user, payload["items"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return order.to_dict()


@router.get("/orders/{order_id}")
def get_order(order_id: int, token: str):
    user = require_user(token)
    order = svc.get(user, order_id)
    if order is None:
        raise HTTPException(404)
    return order.to_dict()


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, token: str):
    user = require_user(token)
    return svc.cancel(user, order_id).to_dict()
