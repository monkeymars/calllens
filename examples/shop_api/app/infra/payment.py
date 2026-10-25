import requests
from app.infra import log


class PaymentGateway:
    url = "https://pay.example/charge"

    def charge(self, user, amount):
        log.info("charging")
        resp = requests.post(self.url, json={"user": user.id, "amount": amount})
        resp.raise_for_status()
        return resp.json()["ref"]

    def refund(self, ref):
        return requests.post(self.url + "/refund", json={"ref": ref})
