# MediChain Wallet Authentication

MediChain uses wallet ownership proofs for user authentication. A wallet
address is an identity, not a password: the user must sign a one-time
MediChain message before the API creates a session.

## User Flow

1. The user clicks **Connect Wallet**.
2. MediChain requests the Bradbury network, chain ID `4221`.
3. The API returns a short-lived challenge.
4. The application displays the exact challenge in a confirmation dialog.
5. MetaMask or another EIP-1193 wallet signs the message with `personal_sign`.
6. The API recovers the signing address and compares it with the requested
   address.
7. The API stores the consumed challenge, creates a revocable JWT session, and
   returns the user's role.

Signing the login message does not submit a GenLayer transaction and does not
spend GEN. The application never requests, reads, or stores the wallet private
key.

## Roles

| Role | Permissions |
| --- | --- |
| `sponsor` | Register trials, submit results, submit whistleblower flags |
| `regulator` | Submit flags and resolve appeals |
| `admin` | All application actions and wallet role administration |

New wallets receive the `sponsor` role. `MEDICHAIN_REGULATOR_WALLETS` and
`MEDICHAIN_ADMIN_WALLETS` are comma-separated allowlists. An administrator can
also assign a role after the wallet has signed in:

```http
PUT /api/admin/users/{wallet_address}/role
Authorization: Bearer <admin-session>
Content-Type: application/json

{"role":"regulator"}
```

The API derives `sponsor_wallet`, `submitter`, and `resolver` from the
authenticated wallet session. Client-submitted identity fields are not trusted.
Sponsors can submit results only for trials registered by their wallet; admins
can perform administrative submissions.

## Session Security

- Challenge messages expire after five minutes by default.
- A consumed challenge cannot be used again.
- JWT sessions expire after one hour by default.
- Sessions are stored in Postgres and can be revoked by signing out.
- The browser keeps the access token in memory only; a page reload requires a
  new wallet signature.
- Account and network changes immediately clear the browser session.
- `JWT_SECRET` must be independent from every GenLayer signing secret.

## Production Environment

Render must have:

```env
MEDICHAIN_WALLET_AUTH_REQUIRED=true
DATABASE_URL=postgresql://...
JWT_SECRET=<at-least-64-random-characters>
JWT_ISSUER=medichain-api
JWT_AUDIENCE=medichain-web
MEDICHAIN_AUTH_DOMAIN=medichain-blush.vercel.app
MEDICHAIN_AUTH_URI=https://medichain-blush.vercel.app
MEDICHAIN_AUTH_CHAIN_ID=4221
MEDICHAIN_ADMIN_WALLETS=<your-admin-wallet>
MEDICHAIN_REGULATOR_WALLETS=<optional-regulator-wallets>
```

`DATABASE_URL` must point to a durable Postgres instance. Do not use a local
SQLite file in production because Render containers are replaceable.

The backend's `PRIVATE_KEY` remains a separate Render-only secret. It signs the
relayed Bradbury contract transaction. The contract's owner is set to that
relayer at deployment, and all contract writes require the owner. This prevents
direct unauthenticated callers from bypassing the API's wallet and role checks.

## Frontend Environment

Vercel needs only public configuration:

```env
API_BASE_URL=https://medichain-q34c.onrender.com
WALLET_CHAIN_ID=4221
WALLET_CHAIN_NAME=GenLayer Bradbury
WALLET_RPC_URL=https://rpc.testnet-chain.genlayer.com
WALLET_EXPLORER_URL=https://explorer.testnet-chain.genlayer.com
```

Never put `PRIVATE_KEY`, `GENLAYER_KEYSTORE_PASSWORD`, `JWT_SECRET`, or
`DATABASE_URL` in Vercel or in `frontend/config.js`.
