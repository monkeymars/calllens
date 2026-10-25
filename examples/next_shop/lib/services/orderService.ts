import { OrderRepo } from "@/lib/repos/orderRepo";
import { ProductRepo } from "@/lib/repos/productRepo";
import { DiscountRepo } from "@/lib/repos/discountRepo";
import { PaymentGateway } from "@/lib/payment";
import * as events from "@/lib/events";
import * as log from "@/lib/log";
import { PricingEngine } from "./pricing";

type User = { id: number };
type Item = { productId: number; sku: string; qty: number };

export class OrderService {
  constructor(
    private orders: OrderRepo,
    private products: ProductRepo,
    private pricing: PricingEngine,
    private payments: PaymentGateway,
  ) {}

  async place(user: User, items: Item[]) {
    log.info(`placing order for ${user.id}`);
    const lines = await Promise.all(
      items.map(async (item) => {
        const product = await this.products.get(item.productId);
        const price = await this.pricing.priceLine(product, item.qty);
        return { sku: item.sku, qty: item.qty, price };
      }),
    );
    const order = await this.orders.create(user.id, lines);
    try {
      await this.payments.charge(order);
    } catch (err) {
      await this.orders.markFailed(order.id);
      throw err;
    }
    for (const item of items) {
      await this.products.decrementStock(item.sku, item.qty);
    }
    events.publish("order.placed", order.id);
    return order;
  }

  async get(user: User, id: number) {
    const order = await this.orders.get(id);
    if (order && order.userId !== user.id) {
      return null;
    }
    return order;
  }

  async listFor(user: User) {
    return this.orders.listFor(user.id);
  }

  async cancel(user: User, id: number) {
    const order = await this.get(user, id);
    if (!order) {
      throw new Error("not found");
    }
    await this.orders.updateStatus(id, "cancelled");
    events.publish("order.cancelled", id);
  }
}

export const orderService = new OrderService(
  new OrderRepo(),
  new ProductRepo(),
  new PricingEngine(new DiscountRepo()),
  new PaymentGateway(),
);
