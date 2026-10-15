import { beforeEach, describe, expect, it } from "vitest";
import type { Draft, PostingSummary } from "../api/types";
import { handleMock, resetMockDb } from "./handler";

beforeEach(() => resetMockDb());

describe("mock API", () => {
  it("hides rejected postings unless include_rejected", async () => {
    const def = (await handleMock("GET", "/api/postings", {}, undefined)).body as PostingSummary[];
    const all = (await handleMock("GET", "/api/postings", { include_rejected: true }, undefined)).body as PostingSummary[];
    expect(def.some((p) => p.filter_state === "rejected")).toBe(false);
    expect(all.length).toBeGreaterThan(def.length);
  });

  it("refuses to approve a LinkedIn draft without recipient", async () => {
    const drafts = (await handleMock("GET", "/api/drafts", {}, undefined)).body as Draft[];
    const noRcpt = drafts.find((d) => d.channel === "linkedin" && !d.recipient)!;
    const res = await handleMock("PATCH", `/api/drafts/${noRcpt.id}`, {}, { status: "approved" });
    expect(res.status).toBe(422);
  });
});
