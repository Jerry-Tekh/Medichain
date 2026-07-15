const fs = require("fs");
const path = require("path");

const rawApiBase = process.env.API_BASE_URL;
if (!rawApiBase) {
  throw new Error("API_BASE_URL is required to build the production frontend");
}

const apiUrl = new URL(rawApiBase);
if (apiUrl.protocol !== "https:") {
  throw new Error("API_BASE_URL must use https for a production build");
}
if (apiUrl.hostname === "localhost" || apiUrl.hostname === "127.0.0.1") {
  throw new Error("API_BASE_URL must not point to localhost in production");
}
if (apiUrl.username || apiUrl.password || apiUrl.search || apiUrl.hash) {
  throw new Error("API_BASE_URL must not contain credentials, query parameters, or a fragment");
}
if (apiUrl.pathname !== "/") {
  throw new Error("API_BASE_URL must contain an origin only, without a path");
}

const walletChainId = Number.parseInt(process.env.WALLET_CHAIN_ID || "4221", 10);
if (!Number.isSafeInteger(walletChainId) || walletChainId <= 0) {
  throw new Error("WALLET_CHAIN_ID must be a positive integer");
}

function secureOrigin(name, fallback) {
  const url = new URL(process.env[name] || fallback);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must be a public HTTPS URL without credentials, query, or fragment`);
  }
  return url.toString().replace(/\/$/, "");
}

const config = {
  API_BASE_URL: apiUrl.origin,
  WALLET_CHAIN_ID: walletChainId,
  WALLET_CHAIN_NAME: process.env.WALLET_CHAIN_NAME || "GenLayer Bradbury",
  WALLET_RPC_URL: secureOrigin(
    "WALLET_RPC_URL",
    "https://rpc.testnet-chain.genlayer.com",
  ),
  WALLET_EXPLORER_URL: secureOrigin(
    "WALLET_EXPLORER_URL",
    "https://explorer.testnet-chain.genlayer.com",
  ),
};
const output = `(function () {\n  window.MEDICHAIN_CONFIG = ${JSON.stringify(config, null, 2)};\n}());\n`;
fs.writeFileSync(path.join(__dirname, "config.js"), output, "utf8");
console.log("Generated frontend/config.js from API_BASE_URL");
