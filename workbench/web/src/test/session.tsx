import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import App from "../App";
import atbench from "../../../../fixtures/workbench/review_bundle_atbench.json";
import hintbench from "../../../../fixtures/workbench/review_bundle_hintbench.json";
import type { ReviewBundle, SessionSample } from "../types";

export function okSample(bundle: ReviewBundle): SessionSample {
  return { sample_id: bundle.sample.sample_id, ok: true, bundle };
}

export async function renderWithSession(samples: SessionSample[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, mode: "batch", samples }),
    })),
  );
  const user = userEvent.setup();
  render(<App />);
  await user.upload(
    screen.getByLabelText("选择 JSONL 文件"),
    new File(["{}"], "batch.jsonl", { type: "application/x-ndjson" }),
  );
  await screen.findByTestId("sample-list");
  return user;
}

// Fixture data belongs to tests; exercise the real upload response boundary.
export async function uploadFixture(user: ReturnType<typeof userEvent.setup>, fixture: "atbench" | "hintbench" | "batch") {
  const samples = fixture === "batch" ? [okSample(atbench as ReviewBundle), okSample(hintbench as ReviewBundle)]
    : [okSample((fixture === "atbench" ? atbench : hintbench) as ReviewBundle)];
  const originalFetch = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: true, status: 200,
    json: async () => ({ ok: true, samples }),
  }));
  await user.upload(screen.getByLabelText("选择 JSONL 文件"),
    new File(["{}"], "samples.jsonl", { type: "application/x-ndjson" }));
  await screen.findByTestId("sample-list");
  vi.stubGlobal("fetch", originalFetch);
}
