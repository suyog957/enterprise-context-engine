import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, humanize } from "../api/client";
import type { ActionAvailability } from "../api/types";
import { ActionTable, Badge, DataTable, shortUri, Trajectory } from "./ui";

const action = (overrides: Partial<ActionAvailability>): ActionAvailability => ({
  action: "CREATE_PURCHASE_ORDER",
  status: "AVAILABLE",
  reason_codes: [],
  explanations: [],
  label: "Create purchase order",
  executable: true,
  execution_endpoint: "POST /requisitions/{id}/create-po",
  preconditions: [],
  effects: [],
  required_permissions: [],
  policy_rules: [],
  ...overrides,
});

describe("ui components", () => {
  it("colours badges by policy outcome", () => {
    render(
      <>
        <Badge value="AVAILABLE" />
        <Badge value="BLOCKED" />
        <Badge value="APPROVAL_REQUIRED" />
      </>,
    );
    expect(screen.getByText("AVAILABLE").className).toContain("badge-good");
    expect(screen.getByText("BLOCKED").className).toContain("badge-bad");
    expect(screen.getByText("APPROVAL REQUIRED").className).toContain("badge-warn");
  });

  it("shows rule references and marks discovery-only actions", () => {
    render(
      <ActionTable
        actions={[
          action({}),
          action({
            action: "EDIT_SUPPLIER",
            label: "Edit supplier master data",
            status: "BLOCKED",
            executable: false,
            explanations: ["Requires ADMIN."],
            policy_rules: [{ rule_id: "AUTH-004", document_id: "POL-000", reason_code: "X" }],
          }),
        ]}
      />,
    );
    expect(screen.getByText("AUTH-004 (POL-000)")).toBeTruthy();
    expect(screen.getByText("discover only")).toBeTruthy();
  });

  it("renders trajectories in order with durations", () => {
    render(
      <Trajectory
        steps={[
          { node: "receive_request", status: "completed", detail: "ok", duration_ms: 0.4 },
          { node: "generate_response", status: "completed", detail: "done", duration_ms: 2 },
        ]}
      />,
    );
    const nodes = Array.from(document.querySelectorAll("[data-node]")).map((el) =>
      el.getAttribute("data-node"),
    );
    expect(nodes).toEqual(["receive_request", "generate_response"]);
  });

  it("shortens URIs and renders empty tables gracefully", () => {
    expect(shortUri("https://example.org/enterprise-context#BusinessPartner")).toBe("BusinessPartner");
    expect(shortUri("PR-1007")).toBe("PR-1007");
    render(<DataTable rows={[]} emptyText="Nothing here." />);
    expect(screen.getByText("Nothing here.")).toBeTruthy();
  });
});

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the principal header to the /api prefix", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await apiRequest("/me", "user-alice");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/me");
    expect((init.headers as Record<string, string>)["X-Dev-Principal"]).toBe("user-alice");
  });

  it("raises readable errors from API detail codes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "requisition_not_found" }), { status: 404 })),
    );
    await expect(apiRequest("/requisitions/PR-1", "user-alice")).rejects.toEqual(
      new ApiError(404, "requisition not found"),
    );
    expect(humanize("raw_graph_query_requires_global_read")).toBe("raw graph query requires global read");
  });
});
