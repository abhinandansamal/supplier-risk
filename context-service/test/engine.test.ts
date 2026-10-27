import { describe, expect, it } from "vitest";

import * as engine from "../src/engine.ts";

describe("engine module", () => {
  it("exports what the conformance runner requires", () => {
    for (const name of ["WorldModel", "entityIdFromName", "normalizeName", "decide"] as const) {
      expect(typeof engine[name]).toBe("function");
    }
  });
});
