import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import session from "../../../../fixtures/workbench/pujiang_race_session.json";
import type { RaceItems } from "../types";
import { RaceAlgorithmItems } from "./RaceAlgorithmItems";

it("renders available archive findings and exact quotes rather than an undefined judgment", () => {
  const race = structuredClone(session.samples[0].bundle.race) as RaceItems;
  race.B3 = { status: "available", risks: [{ step_id: 2, risk_quote: "原始行为 😀\n完整片段", evidence: [{ message_index: 0, quote: "约束原文" }] }] };
  race.C2 = { status: "available", markers: [] };
  race.C3 = { status: "available", failures: [{ step_id: 3, error_type: "timeout", evidence: [{ message_index: 5, quote: "完整错误原文" }] }] };
  render(<RaceAlgorithmItems race={race} items={["B3", "C2", "C3"]} />);
  expect(screen.getByText(/原始行为/).textContent).toBe("原始行为 😀\n完整片段");
  expect(screen.getByText("约束原文")).toBeVisible();
  expect(screen.getByText("无发现（算法已完成）")).toBeVisible();
  expect(screen.getByText(/timeout/)).toBeVisible();
  expect(screen.getByText("完整错误原文")).toBeVisible();
});
