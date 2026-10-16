import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// jsdom has no layout: scrolling is a no-op.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
