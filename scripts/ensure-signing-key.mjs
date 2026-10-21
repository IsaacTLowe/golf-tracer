// Create supabase/signing_keys.json (an ES256 JWT signing key for the local stack) if missing,
// so local tokens are verified via JWKS exactly like hosted Supabase. The file is gitignored.
// The CLI reads the configured keys file before generating, so start from an empty array and
// let it append.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dirname, "..");
const file = join(root, "supabase", "signing_keys.json");

function keyCount() {
  try {
    const keys = JSON.parse(readFileSync(file, "utf8"));
    return Array.isArray(keys) ? keys.length : 0;
  } catch {
    return 0;
  }
}

if (existsSync(file) && keyCount() > 0) {
  console.log("supabase/signing_keys.json exists");
  process.exit(0);
}
writeFileSync(file, "[]\n");
execFileSync("npx", ["supabase", "gen", "signing-key", "--algorithm", "ES256", "--append"], {
  cwd: root,
  stdio: "inherit",
  shell: process.platform === "win32",
});
if (keyCount() < 1) {
  console.error("failed to create a signing key in supabase/signing_keys.json");
  process.exit(1);
}
console.log("wrote supabase/signing_keys.json");
