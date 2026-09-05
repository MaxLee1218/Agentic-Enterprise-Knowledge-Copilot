import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TaskStatus } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import {
  pollingInterval,
  runtimeLabel,
  statusLabel,
  statusTone,
  taskStatuses,
  terminalTaskStatuses,
} from "./status";

describe("authoritative task statuses", () => {
  it.each(taskStatuses)(
    "renders %s as text rather than color alone",
    (status) => {
      render(<StatusBadge status={status} />);
      expect(screen.getByText(statusLabel(status))).toBeVisible();
      expect(statusTone(status)).toMatch(
        /neutral|active|warning|success|danger/,
      );
    },
  );

  it("stops polling terminal states and slows approval waits", () => {
    const terminal: TaskStatus[] = ["COMPLETED", "FAILED", "CANCELLED"];
    for (const status of terminal) {
      expect(terminalTaskStatuses.has(status)).toBe(true);
      expect(pollingInterval(status)).toBe(false);
    }
    expect(pollingInterval("EXECUTING")).toBe(2_000);
    expect(pollingInterval("WAITING_APPROVAL")).toBe(10_000);
    expect(pollingInterval("WAITING_CLARIFICATION")).toBe(10_000);
  });

  it("maps Task and runtime state without exposing Worker or lease internals", () => {
    expect(runtimeLabel("CREATED", "READY")).toBe("Queued");
    expect(runtimeLabel("CREATED", "LEASED")).toBe("Understanding");
    expect(runtimeLabel("UNDERSTANDING", "LEASED")).toBe("Understanding");
    expect(runtimeLabel("PLANNING", "LEASED")).toBe("Planning");
    expect(runtimeLabel("EXECUTING", "LEASED")).toBe("Executing");
    expect(runtimeLabel("EXECUTING", "WAITING_RETRY")).toBe("Retrying");
    expect(runtimeLabel("WAITING_APPROVAL", "SUSPENDED")).toBe(
      "Waiting for approval",
    );
    expect(runtimeLabel("WAITING_CLARIFICATION", "SUSPENDED")).toBe(
      "Waiting for information",
    );
    expect(runtimeLabel("COMPLETED", "FINISHED")).toBe("Completed");
  });
});
