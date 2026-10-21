// Create supabase/signing_keys.json (an ES256 JWT signing key for the local stack) if missing,
// so local tokens are verified via JWKS exactly like hosted Supabase. The file is gitignored.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const file = join(import.meta.dirname, "..", "supabase", "signing_keys.json");
if (existsSync(file)) {
  console.log("supabase/signing_keys.json exists");
  process.exit(0);
}
const out = execFileSync("npx", ["supabase", "gen", "signing-key", "--algorithm", "ES256"], {
  cwd: join(import.meta.dirname, ".."),
  encoding: "utf8",
  shell: process.platform === "win32",
});
if (!existsSync(file)) {
  const start = out.indexOf("{");
  const key = JSON.parse(out.slice(start, out.lastIndexOf("}") + 1));
  writeFileSync(file, JSON.stringify([key], null, 2) + "\n");
}
JSON.parse(readFileSync(file, "utf8"));
console.log("wrote supabase/signing_keys.json");
