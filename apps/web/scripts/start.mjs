import { cpSync, existsSync } from "node:fs";
import process from "node:process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
if (!existsSync(".next/standalone/server.js")) throw new Error("Run npm run build before starting DataMind.");
cpSync(".next/static", ".next/standalone/.next/static", { recursive: true });
process.env.HOSTNAME ||= "127.0.0.1";
await import(pathToFileURL(resolve(".next/standalone/server.js")).href);
