// Fails when a vendored file was edited, added or removed (vendor/*.sha256 lists every file), or
// when vendor/ holds anything but regular files and directories (SEC-085: symlinks).
// Usage: node scripts/verify-vendor.mjs [--dir <vendor dir>] [--write]
//   --write regenerates the manifests; --dir checks another copy (tests).
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const dirFlag = args.indexOf("--dir");
const vendorDir =
  dirFlag >= 0
    ? path.resolve(args[dirFlag + 1])
    : path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "vendor");
const write = args.includes("--write");

let failed = false;

function listFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true, recursive: true })) {
    const relative = path
      .relative(vendorDir, path.join(entry.parentPath, entry.name))
      .split(path.sep)
      .join("/");
    if (entry.isDirectory()) continue;
    if (!entry.isFile()) {
      console.error(`not a regular file: ${relative}`);
      failed = true;
      continue;
    }
    files.push(relative);
  }
  return files.sort();
}

function sha256(file) {
  return createHash("sha256")
    .update(readFileSync(path.join(vendorDir, file)))
    .digest("hex");
}

for (const engine of readdirSync(vendorDir, { withFileTypes: true }).filter((e) =>
  e.isDirectory(),
)) {
  const manifest = path.join(vendorDir, `${engine.name}.sha256`);
  const actual = new Map(listFiles(path.join(vendorDir, engine.name)).map((f) => [f, sha256(f)]));
  if (write) {
    const lines = [...actual].map(([file, hash]) => `${hash}  ${file}`);
    writeFileSync(manifest, `${lines.join("\n")}\n`, "utf-8");
    console.log(`wrote ${path.basename(manifest)} (${String(actual.size)} files)`);
    continue;
  }
  const expected = new Map(
    readFileSync(manifest, "utf-8")
      .split("\n")
      .filter(Boolean)
      .map((line) => {
        const [hash, file] = line.split(/\s{2}/);
        return [file, hash];
      }),
  );
  for (const [file, hash] of expected) {
    if (!actual.has(file)) {
      console.error(`missing: ${file}`);
      failed = true;
    } else if (actual.get(file) !== hash) {
      console.error(`modified: ${file}`);
      failed = true;
    }
  }
  for (const file of actual.keys()) {
    if (!expected.has(file)) {
      console.error(`added: ${file}`);
      failed = true;
    }
  }
  if (!failed) console.log(`${engine.name}: ${String(expected.size)} vendored files unchanged`);
}
process.exit(failed ? 1 : 0);
