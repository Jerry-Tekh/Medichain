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

const config = {
  API_BASE_URL: apiUrl.origin,
};
const output = `(function () {\n  window.MEDICHAIN_CONFIG = ${JSON.stringify(config, null, 2)};\n}());\n`;
fs.writeFileSync(path.join(__dirname, "config.js"), output, "utf8");
console.log("Generated frontend/config.js from API_BASE_URL");
