import { useState } from "react";

export type BusyState = Record<string, number>;

function nextBusyState(state: BusyState, key: string, delta: 1 | -1): BusyState {
  const count = (state[key] ?? 0) + delta;
  if (count > 0) return { ...state, [key]: count };
  const next = { ...state };
  delete next[key];
  return next;
}

export function hasBusy(state: BusyState, key: string) {
  return Boolean(state[key]);
}

export function hasAnyBusy(state: BusyState, keys?: string[]) {
  if (!keys?.length) return Object.keys(state).length > 0;
  return keys.some((key) => hasBusy(state, key));
}

export function useBusyState() {
  const [busy, setBusy] = useState<BusyState>({});

  function startBusy(key: string) {
    setBusy((state) => nextBusyState(state, key, 1));
  }

  function stopBusy(key: string) {
    setBusy((state) => nextBusyState(state, key, -1));
  }

  async function runBusy<T>(key: string, task: () => Promise<T>): Promise<T> {
    startBusy(key);
    try {
      return await task();
    } finally {
      stopBusy(key);
    }
  }

  return { busy, startBusy, stopBusy, runBusy };
}
