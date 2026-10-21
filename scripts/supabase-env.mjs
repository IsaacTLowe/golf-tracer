// Fill .env with the local Supabase keys from `supabase status` (creating .env from
// .env.example if needed). Other values in .env are kept.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dirname, "..");
const envFile = join(root, ".env");
if (!existsSync(envFile)) copyFileSync(join(root, ".env.example"), envFile);

const raw = execFileSync("npx", ["supabase", "status", "-o", "json"], {
  cwd: root,
  encoding: "utf8",
  shell: process.platform === "win32",
});
const status = JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1));
const pick = (...names) => names.map((n) => status[n]).find(Boolean) ?? "";
const values = {
  SUPABASE_URL: pick("API_URL") || "http://127.0.0.1:54321",
  SUPABASE_ANON_KEY: pick("ANON_KEY", "PUBLISHABLE_KEY"),
  SUPABASE_SERVICE_ROLE_KEY: pick("SERVICE_ROLE_KEY", "SECRET_KEY"),
  DATABASE_URL: pick("DB_URL") || "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
};
let text = readFileSync(envFile, "utf8");
for (const [k, v] of Object.entries(values)) {
  const line = `${k}=${v}`;
  text = new RegExp(`^${k}=.*$`, "m").test(text) ? text.replace(new RegExp(`^${k}=.*$`, "m"), line) : `${text.trimEnd()}\n${line}\n`;
}
writeFileSync(envFile, text);
console.log("updated .env with local Supabase keys");
