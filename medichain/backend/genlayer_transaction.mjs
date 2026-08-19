#!/usr/bin/env node
// Sign a GenLayer transaction only after enforcing its exact EVM cost ceiling.

import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";


const GAS_LIMIT_BUFFER_NUMERATOR = 125n;
const GAS_LIMIT_BUFFER_DENOMINATOR = 100n;


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

function requirePositiveBigInt(payload, name) {
  const value = requireString(payload, name);
  if (!/^[1-9][0-9]*$/.test(value)) {
    throw new Error(`${name} must be a positive integer string`);
  }
  return BigInt(value);
}

function decodeAddress(value, calldata) {
  if (typeof value !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(value)) {
    throw new Error("invalid tagged address argument");
  }
  const encoded = new Uint8Array(21);
  encoded[0] = 24;
  for (let index = 0; index < 20; index += 1) {
    encoded[index + 1] = Number.parseInt(
      value.slice(2 + (index * 2), 4 + (index * 2)),
      16,
    );
  }
  return calldata.decode(encoded);
}

function reviveValue(value, calldata) {
  if (Array.isArray(value)) {
    return value.map((item) => reviveValue(item, calldata));
  }
  if (value && typeof value === "object") {
    if (Object.keys(value).length === 1 && "__medichain_int__" in value) {
      const integer = value.__medichain_int__;
      if (typeof integer !== "string" || !/^-?[0-9]+$/.test(integer)) {
        throw new Error("invalid tagged integer argument");
      }
      const parsed = BigInt(integer);
      if (
        parsed >= BigInt(Number.MIN_SAFE_INTEGER)
        && parsed <= BigInt(Number.MAX_SAFE_INTEGER)
      ) {
        return Number(parsed);
      }
      return parsed;
    }
    if (Object.keys(value).length === 1 && "__medichain_address__" in value) {
      return decodeAddress(value.__medichain_address__, calldata);
    }
    return Object.fromEntries(
      Object.entries(value).map(
        ([key, item]) => [key, reviveValue(item, calldata)],
      ),
    );
  }
  return value;
}

function jsonSafe(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(jsonSafe);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, jsonSafe(item)]),
    );
  }
  return value;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function extractContractError(trace) {
  if (!trace) return null;
  const text = typeof trace === "string" ? trace : JSON.stringify(trace);
  const normalized = text
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\\n/g, "\n")
    .replace(/\\"/g, "\"");
  const matches = [...normalized.matchAll(
    /(?:Exception|AssertionError|ValueError|RuntimeError):[ \t]*([^\n"]+)/g,
  )];
  if (matches.length === 0) return null;
  return matches[matches.length - 1][1]
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500) || null;
}

async function readContractError(client, transactionHash) {
  if (typeof client.debugTraceTransaction !== "function") return null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const trace = await client.debugTraceTransaction({
        hash: transactionHash,
        round: 0,
      });
      const reason = extractContractError(trace);
      if (reason) return reason;
    } catch (_) {
      // Receipt metadata remains usable when the debug endpoint is unavailable.
    }
    if (attempt < 2) await sleep(500 * (attempt + 1));
  }
  return null;
}

async function waitForReceipt(client, transactionHash) {
  let lastError;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return await client.waitForTransactionReceipt({
        hash: transactionHash,
        retries: 0,
      });
    } catch (error) {
      lastError = error;
      await sleep(5000);
    }
  }
  throw new Error(
    `receipt unavailable for ${transactionHash}: ${lastError?.message ?? "unknown error"}`,
  );
}

async function main() {
  const payload = JSON.parse(await readStdin());
  const action = requireString(payload, "action");
  const privateKey = requireString(payload, "private_key");
  const rpcUrl = requireString(payload, "rpc_url");
  const network = requireString(payload, "network");
  const maxTransactionCostWei = requirePositiveBigInt(
    payload,
    "max_transaction_cost_wei",
  );

  if (network !== "testnet-bradbury") {
    throw new Error("bounded transactions currently support testnet-bradbury only");
  }

  const modulePath = process.env.GENLAYER_JS_MODULE
    || "/usr/local/lib/node_modules/genlayer/node_modules/genlayer-js/dist/index.js";
  const moduleSpecifier = modulePath.startsWith("file:")
    ? modulePath
    : pathToFileURL(modulePath).href;
  const {
    abi,
    chains,
    createAccount,
    createClient,
  } = await import(moduleSpecifier);

  const normalizedPrivateKey = privateKey.startsWith("0x")
    ? privateKey
    : `0x${privateKey}`;
  const signer = createAccount(normalizedPrivateKey);
  let signedCostCeilingWei = null;
  const boundedSigner = {
    ...signer,
    signTransaction: async (transaction, options) => {
      const estimatedGas = BigInt(transaction.gas ?? 0);
      const gas = (
        estimatedGas * GAS_LIMIT_BUFFER_NUMERATOR
        + GAS_LIMIT_BUFFER_DENOMINATOR
        - 1n
      ) / GAS_LIMIT_BUFFER_DENOMINATOR;
      const gasPrice = BigInt(transaction.gasPrice ?? 0);
      const value = BigInt(transaction.value ?? 0);
      const transactionCostCeilingWei = (gas * gasPrice) + value;
      if (transactionCostCeilingWei > maxTransactionCostWei) {
        throw new Error(
          `transaction cost ceiling ${transactionCostCeilingWei} wei exceeds `
          + `configured maximum ${maxTransactionCostWei} wei`,
        );
      }
      signedCostCeilingWei = transactionCostCeilingWei;
      return signer.signTransaction({ ...transaction, gas }, options);
    },
  };

  const client = createClient({
    chain: chains.testnetBradbury,
    endpoint: rpcUrl,
    account: boundedSigner,
  });
  const calldata = abi?.calldata;

  let transactionHash;
  if (action === "write") {
    transactionHash = await client.writeContract({
      address: requireString(payload, "contract_address"),
      functionName: requireString(payload, "method"),
      args: reviveValue(payload.args ?? [], calldata),
      value: 0n,
    });
  } else if (action === "deploy") {
    const contractPath = requireString(payload, "contract_path");
    const code = await readFile(contractPath, "utf8");
    transactionHash = await client.deployContract({
      code,
      args: reviveValue(payload.args ?? [], calldata),
    });
  } else {
    throw new Error("action must be write or deploy");
  }

  process.stderr.write(`Transaction submitted: ${transactionHash}\n`);
  const receipt = await waitForReceipt(client, transactionHash);
  const contractError = receipt.txExecutionResultName === "FINISHED_WITH_ERROR"
    ? await readContractError(client, transactionHash)
    : null;
  process.stdout.write(`${JSON.stringify(jsonSafe({
    transactionHash,
    signedCostCeilingWei,
    statusName: receipt.statusName,
    resultName: receipt.resultName,
    txExecutionResultName: receipt.txExecutionResultName,
    contractAddress: receipt.txDataDecoded?.contractAddress ?? null,
    contractError,
  }))}\n`);
}


main().catch((error) => {
  process.stderr.write(`Bounded GenLayer transaction failed: ${error.message}\n`);
  process.exitCode = 1;
});
