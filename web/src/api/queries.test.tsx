import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, it, expect, vi } from "vitest";
import type { ReactNode } from "react";
import { useRead } from "./queries";

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { gcTime: 0 } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}
afterEach(() => vi.unstubAllGlobals());
describe("workspace snapshots", () => {
  it("cancels A and never replaces workspace B with a late A response", async () => {
    let resolveA!: (response: Response) => void;
    let signalA: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, options: RequestInit) => {
        if (url.endsWith("/a")) {
          signalA = options.signal as AbortSignal;
          return new Promise<Response>((resolve) => {
            resolveA = resolve;
          });
        }
        return Promise.resolve(
          new Response(JSON.stringify({ workspace: "B" })),
        );
      }),
    );
    const { wrapper, client } = setup();
    const { result, rerender } = renderHook(
      ({ path }) => useRead<{ workspace: string }>(path),
      { initialProps: { path: "/a" }, wrapper },
    );
    rerender({ path: "/b" });
    await waitFor(() => expect(result.current.data?.workspace).toBe("B"));
    expect(signalA?.aborted).toBe(true);
    await act(async () =>
      resolveA(new Response(JSON.stringify({ workspace: "A" }))),
    );
    expect(result.current.data?.workspace).toBe("B");
    client.clear();
  });
  it("retains the last successful snapshot when a refresh disconnects", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ workspace: "A" })))
      .mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetch);
    const { wrapper, client } = setup();
    const { result } = renderHook(
      () => ({ ...useRead<{ workspace: string }>("/a") }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data?.workspace).toBe("A"));
    const updated = result.current.dataUpdatedAt;
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() =>
      expect(result.current.error?.code).toBe("connection_lost"),
    );
    expect(result.current.data?.workspace).toBe("A");
    expect(result.current.dataUpdatedAt).toBe(updated);
    client.clear();
  });
});
