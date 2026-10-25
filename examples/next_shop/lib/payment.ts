import * as log from "./log";

export class PaymentError extends Error {}

export class PaymentGateway {
  async charge(order: { id: number; total: number }) {
    log.info(`charging order ${order.id}`);
    const res = await fetch("https://pay.example/charge", {
      method: "POST",
      body: JSON.stringify({ amount: order.total }),
    });
    if (!res.ok) {
      throw new PaymentError("payment declined");
    }
  }
}
