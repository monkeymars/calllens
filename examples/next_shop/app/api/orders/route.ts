import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/lib/auth";
import { orderService } from "@/lib/services/orderService";

export async function POST(req: NextRequest) {
  const user = await requireUser(req);
  const body = await req.json();
  try {
    const order = await orderService.place(user, body.items);
    return NextResponse.json(order, { status: 201 });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 400 });
  }
}

export async function GET(req: NextRequest) {
  const user = await requireUser(req);
  const orders = await orderService.listFor(user);
  return NextResponse.json(orders);
}
