import * as log from "./log";

export function publish(topic: string, payload: unknown) {
  log.info(`event ${topic}`);
}
