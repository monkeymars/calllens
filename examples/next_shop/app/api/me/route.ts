import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/lib/auth";

export const GET = async (req: NextRequest) => {
  const user = await requireUser(req);
  return NextResponse.json(user);
};
