import { describe, expect, it } from "vitest";

import { ApiError, createApiClient, unwrap } from "../src";

function fakeFetch(handler: (req: Request) => Response | Promise<Response>) {
  const calls: Request[] = [];
  const fn = async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init);
    calls.push(req);
    return handler(req);
  };
  return { fn: fn as typeof fetch, calls };
}

describe("createApiClient", () => {
  it("sends the bearer token and returns data", async () => {
    const f = fakeFetch(() =>
      Response.json({ status: "ok", version: "1", database: "ok" }, { headers: { "content-type": "application/json" } }),
    );
    const api = createApiClient({ baseUrl: "http://api.test/", getAccessToken: async () => "tok", fetch: f.fn });
    const data = await unwrap(api.GET("/health"));
    expect(data.status).toBe("ok");
    expect(f.calls[0]!.url).toBe("http://api.test/health");
    expect(f.calls[0]!.headers.get("Authorization")).toBe("Bearer tok");
  });

  it("omits the header when signed out", async () => {
    const f = fakeFetch(() => Response.json({ status: "ok", version: "1", database: "ok" }));
    const api = createApiClient({ baseUrl: "http://api.test", getAccessToken: async () => null, fetch: f.fn });
    await unwrap(api.GET("/health"));
    expect(f.calls[0]!.headers.get("Authorization")).toBeNull();
  });

  it("turns the error envelope into an ApiError", async () => {
    const f = fakeFetch(() =>
      Response.json(
        { error: { code: "rate_limited", message: "Slow down" } },
        { status: 429, headers: { "Retry-After": "42" } },
      ),
    );
    const api = createApiClient({ baseUrl: "http://api.test", getAccessToken: async () => "t", fetch: f.fn });
    const err = await unwrap(
      api.POST("/v1/clips", { body: { filename: "a.mp4", size_bytes: 1, content_type: "video/mp4" } }),
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("rate_limited");
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).retryAfter).toBe(42);
  });

  it("reports network failures", async () => {
    const api = createApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => null,
      fetch: (async () => {
        throw new TypeError("Failed to fetch");
      }) as typeof fetch,
    });
    const err = await unwrap(api.GET("/health")).catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("network_error");
  });
});
