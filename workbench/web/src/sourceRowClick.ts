import { useRef } from "react";
import type { MouseEvent } from "react";

const DRAG_SLOP_PX = 5;
const INTERACTIVE = "button, a, summary, input, textarea, select";

type ClickPoint = { x: number; y: number };

type RowClick = {
  target: EventTarget | null;
  clientX: number;
  clientY: number;
  defaultPrevented: boolean;
};

export function shouldOpenSourceFromRow(event: RowClick, origin: ClickPoint | null): boolean {
  if (event.defaultPrevented) return false;
  if (origin) {
    const moved =
      Math.abs(event.clientX - origin.x) > DRAG_SLOP_PX ||
      Math.abs(event.clientY - origin.y) > DRAG_SLOP_PX;
    if (moved) return false;
  }
  const target = event.target;
  if (!(target instanceof Element)) return false;
  if (target.closest(INTERACTIVE)) return false;
  return true;
}

export function useSourceRowActivation(onSelect: (eventId: string) => void) {
  const origin = useRef<ClickPoint | null>(null);

  function onMouseDown(event: MouseEvent<HTMLElement>) {
    if (event.button !== 0) return;
    origin.current = { x: event.clientX, y: event.clientY };
  }

  function onClick(event: MouseEvent<HTMLElement>, eventId: string) {
    const start = origin.current;
    origin.current = null;
    if (event.button !== 0) return;
    if (!shouldOpenSourceFromRow(event, start)) return;
    onSelect(eventId);
  }

  return { onMouseDown, onClick };
}
