/**
 * Create a Supabase auth user from the command line.
 *
 * Signup is going away from the UI, so accounts are provisioned here instead.
 * Uses the Supabase Admin API, which needs the service role key — hence this
 * runs only on a developer/operator machine, never in the browser bundle.
 *
 *   pnpm run create-user --email dev@example.com --password 'secret123'
 *   pnpm run create-user --email dev@example.com            # password generated
 *   pnpm run create-user --email dev@example.com --no-confirm
 *
 * The user is email-confirmed by default so they can sign in immediately;
 * pass --no-confirm to leave confirmation pending.
 *
 * Env (read from process env, then frontend/.env.local, then backend/.env):
 *   SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL)
 *   SUPABASE_SERVICE_ROLE_KEY
 */

import { randomBytes } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createClient } from "@supabase/supabase-js";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Minimal KEY=VALUE reader. The env files are hand-maintained and flat, so a
// full dotenv dependency would be more than this needs. Earlier files win, so
// callers can override anything by exporting it in their shell.
function loadEnvFile(path) {
  if (!existsSync(path)) return;
  for (const rawLine of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

function parseArgs(argv) {
  const args = { confirm: true };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    switch (arg) {
      case "--email":
      case "-e":
        args.email = argv[++i];
        break;
      case "--password":
      case "-p":
        args.password = argv[++i];
        break;
      case "--no-confirm":
        args.confirm = false;
        break;
      case "--help":
      case "-h":
        args.help = true;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

const usage = `Usage: npm run create-user -- --email <email> [--password <password>] [--no-confirm]

Options:
  -e, --email <email>        Email address of the user to create (required)
  -p, --password <password>  Password; a strong one is generated when omitted
      --no-confirm           Leave the email unconfirmed (default: confirmed)
  -h, --help                 Show this message`;

function fail(message) {
  console.error(`create-user: ${message}`);
  process.exit(1);
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`create-user: ${error.message}\n\n${usage}`);
    process.exit(1);
  }

  if (args.help) {
    console.log(usage);
    return;
  }

  if (!args.email) fail(`--email is required.\n\n${usage}`);

  loadEnvFile(resolve(frontendDir, ".env.local"));
  loadEnvFile(resolve(frontendDir, "..", "backend", ".env"));

  const supabaseUrl =
    process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl) {
    fail("SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) is not set.");
  }
  if (!serviceRoleKey) {
    fail(
      "SUPABASE_SERVICE_ROLE_KEY is not set. Find it in Supabase Studio → " +
        "Project Settings → API, and put it in backend/.env (never in a " +
        "NEXT_PUBLIC_ variable — those are shipped to the browser)."
    );
  }

  // base64url of 18 bytes — 24 chars, mixed case and digits, no shell-hostile
  // characters, so the printed password is safe to copy and paste anywhere.
  const password = args.password ?? randomBytes(18).toString("base64url");
  const generated = !args.password;

  // No session persistence: this is a one-shot admin call, not a signed-in app.
  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  const { data, error } = await supabase.auth.admin.createUser({
    email: args.email,
    password,
    email_confirm: args.confirm,
  });

  if (error) fail(`could not create user — ${error.message}`);

  console.log(`Created user ${data.user.email}`);
  console.log(`  id:        ${data.user.id}`);
  console.log(`  confirmed: ${data.user.email_confirmed_at ? "yes" : "no"}`);
  if (generated) console.log(`  password:  ${password}`);
}

main().catch((error) => fail(error instanceof Error ? error.message : String(error)));
