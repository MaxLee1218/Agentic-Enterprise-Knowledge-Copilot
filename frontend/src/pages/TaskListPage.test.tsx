import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { accountsPayableTask, task } from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

describe("multi-domain task history", () => {
  it("shows both governed use-case badges without exposing domain-specific payloads", async () => {
    server.use(
      http.get("*/api/v1/tasks", () =>
        HttpResponse.json({
          items: [accountsPayableTask, task],
          total: 2,
          limit: 20,
          offset: 0,
        }),
      ),
    );
    renderApp("/tasks");

    expect(await screen.findByText("Accounts Payable")).toBeVisible();
    expect(screen.getByText("Supplier Quality")).toBeVisible();
    expect(screen.queryByText(/gross_amount/)).not.toBeInTheDocument();
  });
});
