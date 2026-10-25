import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/lib/auth";
import { orderService } from "@/lib/services/orderService";

export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  const user = await requireUser(req);
  await orderService.cancel(user, Number(params.id));
  return NextResponse.json({ ok: true });
}
