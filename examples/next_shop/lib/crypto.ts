export const unsign = (token: string): number => {
  return JSON.parse(Buffer.from(token, "base64").toString()).uid;
};
