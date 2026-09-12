import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ConsoleApiError, getJson } from "./client";
export function useRead<T>(path: string) {
  const failures = useRef(new Map<string, number>());
  const [visible, setVisible] = useState(document.visibilityState !== "hidden");
  useEffect(() => {
    const listener = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  }, []);
  return useQuery<T, ConsoleApiError>({
    queryKey: ["console", path],
    queryFn: async ({ signal }) => {
      try {
        const data = await getJson<T>(path, signal);
        failures.current.set(path, 0);
        return data;
      } catch (error) {
        if (!signal.aborted)
          failures.current.set(path, (failures.current.get(path) ?? 0) + 1);
        throw error;
      }
    },
    retry: false,
    refetchOnWindowFocus: true,
    refetchIntervalInBackground: false,
    refetchInterval: (query) =>
      !visible || (query.state.error && !query.state.error.retryable)
        ? false
        : Math.min(
            30000,
            5000 * 2 ** Math.max(0, (failures.current.get(path) ?? 0) - 1),
          ),
  });
}
