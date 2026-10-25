// The same domain behind an Express router, to show Express-style routes.
import express from "express";
import { requireUser } from "../lib/auth";
import { orderService } from "../lib/services/orderService";

const router = express.Router();

router.get("/orders/:id", async (req, res) => {
  const user = await requireUser(req as any);
  res.json(await orderService.get(user, Number(req.params.id)));
});

router.post("/orders/:id/cancel", cancelOrder);

async function cancelOrder(req: express.Request, res: express.Response) {
  const user = await requireUser(req as any);
  await orderService.cancel(user, Number(req.params.id));
  res.json({ ok: true });
}

export default router;
