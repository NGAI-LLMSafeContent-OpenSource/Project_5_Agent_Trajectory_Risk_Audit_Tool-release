import { describe, expect, it } from "vitest";
import { shouldOpenSourceFromRow } from "./sourceRowClick";

function clickAt(target: Element, x: number, y: number) {
  return { target, clientX: x, clientY: y, defaultPrevented: false };
}

describe("source row activation", () => {
  it("opens when the pointer stays on the row text", () => {
    const text = document.createElement("p");
    expect(shouldOpenSourceFromRow(clickAt(text, 12, 11), { x: 10, y: 10 })).toBe(true);
  });

  it("opens a click that never recorded a pointer origin", () => {
    const text = document.createElement("p");
    expect(shouldOpenSourceFromRow(clickAt(text, 4, 4), null)).toBe(true);
  });

  it("stays put when the pointer moves far enough to select text", () => {
    const text = document.createElement("p");
    expect(shouldOpenSourceFromRow(clickAt(text, 16, 10), { x: 10, y: 10 })).toBe(false);
    expect(shouldOpenSourceFromRow(clickAt(text, 10, 16), { x: 10, y: 10 })).toBe(false);
  });

  it("leaves buttons, links, and disclosure summaries to their own controls", () => {
    const button = document.createElement("button");
    const link = document.createElement("a");
    const summary = document.createElement("summary");
    const label = document.createElement("span");
    summary.append(label);

    expect(shouldOpenSourceFromRow(clickAt(button, 1, 1), { x: 1, y: 1 })).toBe(false);
    expect(shouldOpenSourceFromRow(clickAt(link, 1, 1), { x: 1, y: 1 })).toBe(false);
    expect(shouldOpenSourceFromRow(clickAt(label, 1, 1), { x: 1, y: 1 })).toBe(false);
  });
});
