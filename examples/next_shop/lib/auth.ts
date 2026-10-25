import { NextRequest } from "next/server";
import * as cache from "./cache";
import { unsign } from "./crypto";
import { UserRepo } from "./repos/userRepo";

const users = new UserRepo();

export async function requireUser(req: NextRequest) {
  const token = req.headers.get("authorization") ?? "";
  let uid = cache.get(`session:${token}`);
  if (uid === undefined) {
    uid = unsign(token);
  }
  const user = await users.get(uid);
  if (!user) throw new Error("unauthorized");
  return user;
}
