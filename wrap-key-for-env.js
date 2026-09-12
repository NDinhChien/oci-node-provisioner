#!/usr/bin/env node
/**
 * Wraps a PEM private key file into a single-line, \n-escaped string
 * suitable for pasting into a .env file as OCI_PRIVATE_KEY="...".
 *
 * Usage:
 *   node wrap-key-for-env.js /path/to/your_private_key.pem
 *
 * Output:
 *   Prints the line to paste directly into your .env file.
 *   Also writes it to key_for_env.txt so you can copy from a file
 *   instead of the terminal if needed.
 */

const fs = require("fs");
const path = require("path");

function wrapKey(pemPath) {
  if (!fs.existsSync(pemPath)) {
    throw new Error(`No such file: ${pemPath}`);
  }

  const content = fs.readFileSync(pemPath, "utf8");

  // Normalize line endings, drop empty lines, then join with a literal
  // \n so it survives being stored as a single-line value in a .env file.
  const lines = content
    .split(/\r\n|\r|\n/)
    .filter((line) => line.trim() !== "");

  const escaped = lines.join("\\n");

  return `OCI_PRIVATE_KEY="${escaped}"`;
}

function main() {
  const args = process.argv.slice(2);

  if (args.length !== 1) {
    console.log("Usage: node wrap-key-for-env.js /path/to/your_private_key.pem");
    process.exit(1);
  }

  const pemPath = args[0];
  let envLine;

  try {
    envLine = wrapKey(pemPath);
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }

  console.log("\n--- Paste this line into your .env file ---\n");
  console.log(envLine);
  console.log("\n--- End ---\n");

  const outFile = path.join(process.cwd(), "key_for_env.txt");
  fs.writeFileSync(outFile, envLine + "\n", { encoding: "utf8" });

  console.log(`Also saved to: ${outFile}`);
  console.log("Delete that file once you've copied it — it contains your private key.");
}

main();
