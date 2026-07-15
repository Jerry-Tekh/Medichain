# MediChain Production Deployment

MediChain has two runtime modes:

- `local`: persistent JSON-backed simulator for development only.
- `genlayer`: production mode that proxies API calls to the deployed Bradbury contract.

Production must use `genlayer`. The deployed Bradbury contract is:

```text
0xebb0590f54Aaf1bA1Cfd544325307759c1F79e50
```

## Required Backend Environment

```env
MEDICHAIN_ENV=production
MEDICHAIN_BACKEND_MODE=genlayer
MEDICHAIN_CONTRACT_ADDRESS=0xebb0590f54Aaf1bA1Cfd544325307759c1F79e50
GENLAYER_RPC_URL=https://rpc-bradbury.genlayer.com
GENLAYER_NETWORK=testnet-bradbury
GENLAYER_ACCOUNT_NAME=medichain-production
GENLAYER_CLI_COMMAND=genlayer
GENLAYER_CLI_FEES={"distribution":{"leaderTimeunitsAllocation":"1000","validatorTimeunitsAllocation":"1000","rotations":["0"]}}
GENLAYER_KEYSTORE_PASSWORD=at-least-eight-random-characters
PRIVATE_KEY=0x...
GENLAYER_TIMEOUT_SECONDS=600
ALLOWED_ORIGINS=https://your-frontend-domain.com
ALLOWED_HOSTS=your-api-domain.com
API_TOKENS=at-least-32-random-characters
MEDICHAIN_REQUIRE_WRITE_AUTH=true
```

Generate independent backend secrets with a local secret generator such as:

```bash
openssl rand -hex 32
```

Use separate generated values for `GENLAYER_KEYSTORE_PASSWORD` and
`API_TOKENS`. The backend imports `PRIVATE_KEY` into the named encrypted
GenLayer CLI keystore on its first Bradbury request. The setup helper uses
the CLI's existing `ethers` package, so it adds no runtime dependency. The key
and password are provided over standard input, not command-line arguments.
Do not
expose `PRIVATE_KEY`,
`GENLAYER_KEYSTORE_PASSWORD`, or `API_TOKENS` to the frontend. Those belong
only in the backend deployment environment.

`TREASURE_ADDRESS` is only a constructor argument when deploying a new
contract. It is not needed by this API because the contract is already
deployed. `MEDICHAIN_CONTRACT_ADDRESS` is the deployed contract address the
API calls.

Production startup rejects wildcard or localhost origins/hosts, non-HTTPS
RPC/origin values, weak API tokens, malformed private keys, a non-Bradbury
network, disabled write authentication, and local simulator mode.

## Frontend Configuration

Set this environment variable in the frontend deployment:

```env
API_BASE_URL=https://your-api-domain.com
```

`frontend/build-config.js` validates the URL and generates `frontend/config.js`
during the production build. The frontend reads `window.MEDICHAIN_CONFIG` from
that generated file.
For a same-origin development deployment, the checked-in default is enough:

```js
window.MEDICHAIN_CONFIG = {
  API_BASE_URL: window.location.origin,
};
```

For a separate API host, set:

```js
window.MEDICHAIN_CONFIG = {
  API_BASE_URL: "https://your-api-domain.com",
};
```

Authorized operators enter a write token in the UI. It is kept only in the
page's JavaScript memory, sent as a bearer token on write requests, and lost
on reload. It is never included in `config.js`, local storage, or session
storage.

For Vercel, set the project root directory to `medichain/frontend` and add
`API_BASE_URL` under project environment variables. `vercel.json` runs the
zero-dependency config build and installs CSP, framing, referrer, MIME, and
browser-permission response headers.

## Backend Container

Build the backend image from the repository root:

```bash
docker build -t medichain-api .
```

Run it with production environment variables:

```bash
docker run --rm -p 8000:8000 --env-file .env.production medichain-api
```

The image installs the pinned `genlayer@0.39.2` CLI at build time, installs
only production Python packages, runs as the unprivileged `medichain` user,
and starts one bounded-concurrency Uvicorn worker. One worker is intentional:
Bradbury writes and the CLI keystore must be serialized within the process.
The Render Blueprint likewise fixes the service at one instance so separate
containers cannot race transactions from the same signer.

## Render Backend

The repository includes `render.yaml`. Create a Render Blueprint from the
repository and set these `sync: false` variables in the Render dashboard:

```env
ALLOWED_ORIGINS=https://your-vercel-domain.com
ALLOWED_HOSTS=your-render-domain.onrender.com
API_TOKENS=<generated-secret>
PRIVATE_KEY=<your-Bradbury-funded-private-key>
GENLAYER_KEYSTORE_PASSWORD=<different-generated-secret>
```

Do not include schemes in `ALLOWED_HOSTS`. For multiple frontend origins or
API tokens, use comma-separated values. The Bradbury signing account must
have enough testnet GEN to pay transaction fees.

After the backend is live, set the Vercel frontend variable:

```env
API_BASE_URL=https://your-render-domain.onrender.com
```

Redeploy the frontend after changing `API_BASE_URL`, then enter one value from
`API_TOKENS` into the UI's write-key field.

## Verification

No-dependency static checks:

```bash
python3 medichain/scripts/check_production_readiness.py
python3 medichain/scripts/check_genlayer_adapter.py
python3 -m py_compile \
  medichain/backend/config.py \
  medichain/backend/genlayer_client.py \
  medichain/backend/main.py \
  medichain/backend/persistence.py \
  medichain/backend/start.py \
  medichain/contract/genlayer_adapter.py \
  medichain/scripts/check_production_readiness.py
```

With dependencies installed in the deployment environment:

```bash
pytest medichain/tests/test_integration.py
```

Runtime checks after deployment:

```bash
curl -fsS https://your-api-domain.com/api/health
curl -fsS https://your-api-domain.com/api/ready
```

`/api/health` proves that the API process started. `/api/ready` performs a
read-only `get_treasury_address` call against the deployed Bradbury contract.
Use `/api/ready` for the platform health check.

## Safety Checks Enforced at Startup

When `MEDICHAIN_ENV=production`, the API refuses to boot if:

- `MEDICHAIN_BACKEND_MODE` is not `genlayer`
- `ALLOWED_ORIGINS` contains `*`
- `ALLOWED_ORIGINS` uses localhost
- `ALLOWED_HOSTS` contains `*` or localhost
- `MEDICHAIN_REQUIRE_WRITE_AUTH` is disabled
- write authentication is enabled but `API_TOKENS` is empty
- GenLayer mode is missing `MEDICHAIN_CONTRACT_ADDRESS`
- production GenLayer writes are missing `PRIVATE_KEY` or `GENLAYER_KEYSTORE_PASSWORD`
