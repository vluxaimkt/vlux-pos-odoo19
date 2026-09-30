import { useEffect, useRef, useState } from "preact/hooks";

/** The browser's idea of connectivity; the API calls are the real test. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

/** Run `task` now and every `ms`, never two at a time. */
export function useInterval(task: () => Promise<unknown>, ms: number, enabled = true): void {
  const running = useRef(false);
  const latest = useRef(task);
  latest.current = task;
  useEffect(() => {
    if (!enabled) return;
    const tick = async () => {
      if (running.current) return;
      running.current = true;
      try {
        await latest.current();
      } catch {
        // Each task records its own failures; the timer just keeps going.
      } finally {
        running.current = false;
      }
    };
    void tick();
    const id = setInterval(tick, ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
}
