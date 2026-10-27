import { defineConfig } from "vitest/config";

// Only our tests; the vendored reference has its own suite (run via the conformance runner).
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    reporters: ["default", "junit"],
    outputFile: { junit: "reports/junit.xml" },
  },
});
