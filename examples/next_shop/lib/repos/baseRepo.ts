import * as db from "../db";

export abstract class BaseRepo<T> {
  protected abstract table: string;

  async get(id: number): Promise<T | undefined> {
    return db.queryOne(`select * from ${this.table} where id = $1`, [id]);
  }
}
