import { Pool } from "pg";

const pool = new Pool();

export async function query(sql: string, params: unknown[] = []) {
  const result = await pool.query(sql, params);
  return result.rows;
}

export async function queryOne(sql: string, params: unknown[] = []) {
  const rows = await query(sql, params);
  return rows[0];
}

export async function execute(sql: string, params: unknown[] = []) {
  await pool.query(sql, params);
}
