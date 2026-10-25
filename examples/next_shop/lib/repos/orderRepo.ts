import * as db from "../db";
import { BaseRepo } from "./baseRepo";

type Line = { sku: string; qty: number; price: number };

export class OrderRepo extends BaseRepo<{ id: number; userId: number; total: number }> {
  protected table = "orders";

  async create(userId: number, lines: Line[]) {
    const order = await db.queryOne("insert into orders (user_id) values ($1) returning *", [userId]);
    for (const line of lines) {
      await db.execute("insert into order_lines values ($1, $2, $3)", [order.id, line.sku, line.qty]);
    }
    return order;
  }

  async listFor(userId: number) {
    return db.query("select * from orders where user_id = $1", [userId]);
  }

  async markFailed(id: number) {
    await this.updateStatus(id, "failed");
  }

  async updateStatus(id: number, status: string) {
    await db.execute("update orders set status = $1 where id = $2", [status, id]);
  }
}
