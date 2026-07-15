#!/usr/bin/env node
// Create the encrypted keystore expected by genlayer-cli from stdin JSON.

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";


async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function requireString(payload, name) {
  const value = payload[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${name} is required`);
  }
  return value;
}

async function readConfig(configPath) {
  try {
    const parsed = JSON.parse(await readFile(configPath, "utf8"));
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("GenLayer config must contain a JSON object");
    }
    return parsed;
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw error;
  }
}

async function atomicWrite(filePath, content) {
  const temporaryPath = `${filePath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, content, { encoding: "utf8", mode: 0o600 });
  await rename(temporaryPath, filePath);
}

async function main() {
  const payload = JSON.parse(await readStdin());
  const privateKey = requireString(payload, "private_key");
  const password = requireString(payload, "password");
  const accountName = requireString(payload, "account_name");
  const network = requireString(payload, "network");

  if (!/^[A-Za-z0-9_-]{1,64}$/.test(accountName)) {
    throw new Error("account_name contains unsupported characters");
  }
  if (password.length < 8) {
    throw new Error("password must contain at least 8 characters");
  }

  const ethersModule = process.env.GENLAYER_ETHERS_MODULE
    || "/usr/local/lib/node_modules/genlayer/node_modules/ethers/lib.esm/index.js";
  const moduleSpecifier = ethersModule.startsWith("file:")
    ? ethersModule
    : pathToFileURL(ethersModule).href;
  const { Wallet } = await import(moduleSpecifier);
  const wallet = new Wallet(privateKey);
  const encryptedKeystore = await wallet.encrypt(password);

  const genlayerDirectory = path.join(os.homedir(), ".genlayer");
  const keystoreDirectory = path.join(genlayerDirectory, "keystores");
  const configPath = path.join(genlayerDirectory, "genlayer-config.json");
  const keystorePath = path.join(keystoreDirectory, `${accountName}.json`);
  await mkdir(keystoreDirectory, { recursive: true, mode: 0o700 });

  const config = await readConfig(configPath);
  config.activeAccount = accountName;
  config.network = network;
  await atomicWrite(keystorePath, encryptedKeystore);
  await atomicWrite(configPath, `${JSON.stringify(config, null, 2)}\n`);

  process.stdout.write(`GenLayer signer ready: ${wallet.address}\n`);
}


main().catch((error) => {
  process.stderr.write(`GenLayer signer setup failed: ${error.message}\n`);
  process.exitCode = 1;
});
