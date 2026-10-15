import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../auth", () => ({ getAccessToken: vi.fn().mockResolvedValue("tok-123") }));

import { ApiError, apiRequest } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("apiRequest", () => {
  it("adds the bearer token and serialises query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await apiRequest("/api/postings", { query: { priority: "Alta", q: "", include_rejected: true } });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/postings?priority=Alta&include_rejected=true");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok-123" });
  });

  it("throws ApiError with the FastAPI detail message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({ detail: "Non trovato" }), { status: 404 })));
    await expect(apiRequest("/api/postings/9/pack")).rejects.toMatchObject({ status: 404, message: "Non trovato" });
    await expect(apiRequest("/api/postings/9/pack")).rejects.toBeInstanceOf(ApiError);
  });
});
