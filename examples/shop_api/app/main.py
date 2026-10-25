from fastapi import FastAPI
from app.api.orders import router as orders_router
from app.api.users import router as users_router

app = FastAPI()
app.include_router(orders_router)
app.include_router(users_router)


def main():
    import uvicorn
    uvicorn.run(app)
