import { spawnSync } from "node:child_process";
import { appendFileSync, cpSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { tempDir } from "./helpers.ts";

const SCRIPT = path.resolve("scripts/verify-vendor.mjs");

/** A copy of vendor/ (with its manifest) that a test may break. */
function vendorCopy(): string {
  const dir = path.join(tempDir(), "vendor");
  cpSync(path.resolve("vendor"), dir, { recursive: true });
  return dir;
}

function verify(dir: string): number | null {
  return spawnSync(process.execPath, [SCRIPT, "--dir", dir], { encoding: "utf-8" }).status;
}

describe("verify-vendor", () => {
  it("passes on the unchanged vendored code", () => {
    expect(verify(vendorCopy())).toBe(0);
  });

  it("fails on an edited file", () => {
    const dir = vendorCopy();
    appendFileSync(path.join(dir, "world-model/reference/src/resolve.ts"), "// edit\n");
    expect(verify(dir)).toBe(1);
  });

  it("fails on an added file", () => {
    const dir = vendorCopy();
    writeFileSync(path.join(dir, "world-model/reference/src/extra.ts"), "export {};\n");
    expect(verify(dir)).toBe(1);
  });

  it("fails on an added symlink (SEC-085)", () => {
    const dir = vendorCopy();
    symlinkSync("/etc/hosts", path.join(dir, "world-model/reference/src/extra.ts"));
    expect(verify(dir)).toBe(1);
  });
});
