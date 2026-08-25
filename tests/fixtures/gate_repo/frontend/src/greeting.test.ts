import { describe, expect, it } from "vitest";
import { backendUrl } from "./greeting";

describe("backendUrl", () => {
  it("defaults to localhost:8080", () => {
    expect(backendUrl()).toBe("http://localhost:8080");
  });

  it("is a well-formed http url", () => {
    expect(backendUrl()).toMatch(/^http:\/\//);
  });
});
