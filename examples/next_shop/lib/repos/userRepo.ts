import { BaseRepo } from "./baseRepo";

export class UserRepo extends BaseRepo<{ id: number; name: string }> {
  protected table = "users";
}
