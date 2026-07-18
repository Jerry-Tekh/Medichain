# MediChain Production Deployment

MediChain runs the static frontend on Vercel and the Docker API on Render.
Production API writes are authorized by wallet-signature sessions and relayed
to the owner-restricted GenLayer Bradbury contract:

```text
0x6207D84A866919Daa876b902E3ab51F5560F10CB
```

## Render

Deploy the repository root as a Docker service. The checked-in `render.yaml`
already sets the public and non-secret production configuration for:

- GenLayer Bradbury network, RPC, transaction-cost ceiling, CLI, and contract
  address
- wallet authentication domain, URI, chain ID, and session lifetimes
- `https://medichain-blush.vercel.app` as the only browser origin
- `medichain-q34c.onrender.com` as the allowed API host
- one service instance to serialize writes from the relayer account

Set these five values in the Render dashboard:

```env
DATABASE_URL=<Render Postgres internal connection string>
JWT_SECRET=<at least 64 random characters>
MEDICHAIN_ADMIN_WALLETS=<comma-separated admin wallet addresses>
PRIVATE_KEY=<Bradbury relayer private key>
GENLAYER_KEYSTORE_PASSWORD=<independent random secret, at least 8 characters>
```

If the existing Render service was created manually instead of from
`render.yaml`, set the complete block below in **Environment**:

```env
MEDICHAIN_ENV=production
MEDICHAIN_BACKEND_MODE=genlayer
MEDICHAIN_CONTRACT_ADDRESS=0x6207D84A866919Daa876b902E3ab51F5560F10CB
GENLAYER_RPC_URL=https://rpc-bradbury.genlayer.com
GENLAYER_NETWORK=testnet-bradbury
GENLAYER_ACCOUNT_NAME=medichain-production
GENLAYER_CLI_COMMAND=genlayer
GENLAYER_MAX_TRANSACTION_COST_WEI=500000000000000000
GENLAYER_TIMEOUT_SECONDS=600
MEDICHAIN_WALLET_AUTH_REQUIRED=true
DATABASE_URL=<Render Postgres internal connection string>
JWT_SECRET=<at least 64 random characters>
JWT_ISSUER=medichain-api
JWT_AUDIENCE=medichain-web
MEDICHAIN_AUTH_DOMAIN=medichain-blush.vercel.app
MEDICHAIN_AUTH_URI=https://medichain-blush.vercel.app
MEDICHAIN_AUTH_CHAIN_ID=4221
MEDICHAIN_AUTH_CHALLENGE_TTL_SECONDS=300
MEDICHAIN_AUTH_SESSION_TTL_SECONDS=3600
MEDICHAIN_ADMIN_WALLETS=<comma-separated admin wallet addresses>
ALLOWED_ORIGINS=https://medichain-blush.vercel.app
ALLOWED_HOSTS=medichain-q34c.onrender.com
PRIVATE_KEY=<Bradbury relayer private key>
GENLAYER_KEYSTORE_PASSWORD=<independent random secret, at least 8 characters>
```

Then confirm **Settings** uses branch `main`, Dockerfile path `./Dockerfile`,
repository-root context, and automatic deploys on commit. Select **Manual
Deploy > Deploy latest commit** after saving the environment. If the release
fails, the first startup error in the Render logs names the missing or invalid
variable.

After deployment, `/api/health` must report
`0x6207D84A866919Daa876b902E3ab51F5560F10CB`. A dashboard variable can
override `render.yaml`, so a healthy service that reports any previous
contract address has not completed the production switch.

Create a Render Postgres database in the same region as the web service and use
its internal connection string for `DATABASE_URL`. Production startup rejects
SQLite because the container filesystem is replaceable.

Generate independent secrets locally:

```bash
openssl rand -hex 32
```

Use different generated values for `JWT_SECRET` and
`GENLAYER_KEYSTORE_PASSWORD`. `JWT_SECRET` must also differ from `PRIVATE_KEY`.
The private key must control the deployed contract owner:

```text
0x1847d40a1fc2b69101d943f23ea35bd3774889d7
```

Optional regulator wallets can be added later:

```env
MEDICHAIN_REGULATOR_WALLETS=0x...,0x...
```

Never expose `PRIVATE_KEY`, `GENLAYER_KEYSTORE_PASSWORD`, `JWT_SECRET`, or
`DATABASE_URL` to Vercel. `TREASURE_ADDRESS` is used only when deploying a new
contract and is not a backend runtime variable.

Every deployment and contract write is signed through the bounded transaction
runner. It adds a gas-limit safety buffer, calculates `gas * gasPrice + value`
before signing, and refuses any transaction whose signed ceiling exceeds
`500000000000000000` wei (`0.5 GEN`). Bradbury reads retry transient RPC
transport failures without resubmitting writes.

## Vercel

Set the Vercel project root to `medichain/frontend` and configure:

```env
API_BASE_URL=https://medichain-q34c.onrender.com
WALLET_CHAIN_ID=4221
WALLET_CHAIN_NAME=GenLayer Bradbury
WALLET_RPC_URL=https://rpc.testnet-chain.genlayer.com
WALLET_EXPLORER_URL=https://explorer.testnet-chain.genlayer.com
```

`API_BASE_URL` is required. The wallet variables have checked-in Bradbury
defaults, but setting them explicitly keeps Production and Preview builds
consistent. Redeploy Vercel after changing any build variable.

Users do not enter an application key. They connect an EIP-1193 wallet, review
the one-time login message, and sign it. The backend verifies the signature,
creates a short-lived revocable JWT session, and derives the actor identity
from that session.

## Container

The root `Dockerfile`:

- pins `genlayer@0.39.2`
- installs only production Python packages
- runs as the unprivileged `medichain` user
- starts one bounded-concurrency Uvicorn worker

Build and run it locally with a complete production environment:

```bash
docker build -t medichain-api .
docker run --rm -p 8000:8000 --env-file .env.production medichain-api
```

## Verification

Run the no-install checks before pushing:

```bash
python3 medichain/scripts/check_production_readiness.py
python3 medichain/scripts/check_genlayer_adapter.py
python3 medichain/tests/test_production_support.py
python3 -m py_compile \
  medichain/backend/auth_store.py \
  medichain/backend/config.py \
  medichain/backend/genlayer_client.py \
  medichain/backend/main.py \
  medichain/backend/persistence.py \
  medichain/backend/start.py \
  medichain/backend/wallet_auth.py \
  medichain/contract/genlayer_adapter.py
```

After Render redeploys:

```bash
curl -fsS https://medichain-q34c.onrender.com/api/health
curl -fsS https://medichain-q34c.onrender.com/api/ready
curl -i -X OPTIONS \
  -H 'Origin: https://medichain-blush.vercel.app' \
  -H 'Access-Control-Request-Method: POST' \
  https://medichain-q34c.onrender.com/api/auth/challenge
```

`/api/health` confirms the process is running. `/api/ready` checks Postgres,
reads the Bradbury treasury and owner, and requires the owner to match the
configured relayer. It is the Render health-check path. The CORS preflight
must allow only the Vercel origin.

Then open the Vercel application and complete one wallet login:

1. Connect the intended wallet.
2. Approve switching to Bradbury chain ID `4221`.
3. Review and sign the displayed one-time message.
4. Confirm the connected wallet and assigned role appear.
5. Register a test trial as a sponsor or admin.
6. Confirm the trial appears after refreshing the dashboard.

Trial registration fetches the official ClinicalTrials.gov API record in the
backend, reduces it to a canonical protocol snapshot, and sends that snapshot
to the contract. The contract deterministically validates the NCT identifier,
study title, enrollment, and primary outcomes before storing the immutable
registration snapshot. This avoids Bradbury web-render timeouts during
registration while retaining validator consensus over the stored state.

Result submission uses the same bounded snapshot boundary. The backend fetches
the current ClinicalTrials.gov record plus the publication and optional
preprint, rejects private or non-global destinations and unsupported content,
caps each response, and strips HTML to readable text. The contract binds each
snapshot to its submitted URL and runs the integrity assessment through
Bradbury's non-deterministic execution. Validators independently rerun one
structured assessment and deterministic code compares the state-changing
decision fields, without waiting for GenVM web rendering or making an
additional comparator LLM call.

## Startup Guards

Production refuses to start when:

- the backend mode is not `genlayer`
- CORS origins or allowed hosts are wildcard, localhost, or malformed
- wallet authentication is disabled
- `DATABASE_URL` is absent or is not PostgreSQL
- `JWT_SECRET` is shorter than 64 characters or reuses `PRIVATE_KEY`
- the auth URI does not match the configured HTTPS frontend origin
- no admin wallet is configured
- the wallet chain is not Bradbury chain ID `4221`
- the contract address, RPC, relayer key, or keystore password is invalid
- the GenLayer network is not `testnet-bradbury`
