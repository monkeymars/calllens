import * as db from "../db";
import { BaseRepo } from "./baseRepo";

export class ProductRepo extends BaseRepo<{ id: number; sku: string; price: number }> {
  protected table = "products";

  async decrementStock(sku: string, qty: number) {
    await db.execute("update products set stock = stock - $1 where sku = $2", [qty, sku]);
  }
}
