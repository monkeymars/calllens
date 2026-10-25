import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/lib/auth";
import { orderService } from "@/lib/services/orderService";

export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  const user = await requireUser(req);
  const order = await orderService.get(user, Number(params.id));
  if (!order) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  return NextResponse.json(order);
}
