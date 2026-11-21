import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver; Radix popover positioning requires one.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom does not implement scrolling.
Element.prototype.scrollIntoView ??= () => {};
