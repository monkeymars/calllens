const store = new Map<string, number>();

export function get(key: string): number | undefined {
  return store.get(key);
}

export function set(key: string, value: number) {
  store.set(key, value);
}
