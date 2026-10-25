import * as db from "../db";

export class DiscountRepo {
  async bestFor(sku: string) {
    const rows = await db.query("select * from discounts where sku = $1", [sku]);
    return rows[0];
  }
}
