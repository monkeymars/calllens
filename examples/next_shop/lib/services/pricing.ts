import { DiscountRepo } from "@/lib/repos/discountRepo";

export class PricingEngine {
  constructor(private discounts: DiscountRepo) {}

  async priceLine(product: { sku: string; price: number }, qty: number) {
    const discount = await this.discounts.bestFor(product.sku);
    return this.apply(product.price * qty, discount);
  }

  private apply(amount: number, discount?: { percent: number }) {
    if (discount) {
      return amount * (1 - discount.percent / 100);
    }
    return amount;
  }
}
