#!/usr/bin/env python3
"""No-dependency production-readiness checks for MediChain."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def assert_backend_config() -> None:
    main = read("medichain/backend/main.py")
    config = read("medichain/backend/config.py")
    assert 'allow_origins=["*"]' not in main
    assert "allow_origins=list(settings.allowed_origins)" in main
    assert "current_wallet" in main
    assert "/api/auth/challenge" in main
    assert "WalletAuthService" in main
    assert "GenLayerCliGateway" in main
    assert "PersistentMediChainContract" in main
    assert "production requires MEDICHAIN_BACKEND_MODE=genlayer" in config
    assert "ALLOWED_ORIGINS must not contain '*'" in config
    assert "DATABASE_URL is required in production" in config
    assert "MEDICHAIN_ADMIN_WALLETS" in config


def assert_frontend_config() -> None:
    html = read("medichain/frontend/index.html")
    app = read("medichain/frontend/app.js")
    config = read("medichain/frontend/config.js")
    build_config = read("medichain/frontend/build-config.js")
    vercel = read("medichain/frontend/vercel.json")
    assert 'value="http://localhost:8000"' not in html
    assert '<script src="config.js"></script>' in html
    assert "MEDICHAIN_CONFIG" in config
    assert "WRITE_API_TOKEN" not in config
    assert "MEDICHAIN_CONFIG" in app
    assert "Authorization" in app
    assert "personal_sign" in app
    assert "Connect Wallet" in html
    assert "apiToken" not in html
    assert "sessionStorage" not in app
    assert "localStorage" not in app
    assert "API_BASE_URL" in build_config
    assert "localhost in production" in build_config
    assert "node build-config.js" in vercel


def assert_env_template() -> None:
    env = read(".env.example")
    required = [
        "MEDICHAIN_ENV=production",
        "MEDICHAIN_BACKEND_MODE=genlayer",
        "MEDICHAIN_CONTRACT_ADDRESS=",
        "GENLAYER_RPC_URL=",
        "GENLAYER_NETWORK=",
        "GENLAYER_ACCOUNT_NAME=",
        "GENLAYER_KEYSTORE_PASSWORD=",
        "MEDICHAIN_WALLET_AUTH_REQUIRED=true",
        "DATABASE_URL=",
        "JWT_SECRET=",
        "MEDICHAIN_AUTH_DOMAIN=",
        "MEDICHAIN_AUTH_URI=",
        "MEDICHAIN_AUTH_CHAIN_ID=4221",
        "MEDICHAIN_ADMIN_WALLETS=",
        "ALLOWED_ORIGINS=",
        "ALLOWED_HOSTS=",
        "MEDICHAIN_STATE_PATH=",
        "API_BASE_URL=",
    ]
    for item in required:
        assert item in env, f"missing {item} in .env.example"


def assert_deployment_config() -> None:
    dockerfile = read("Dockerfile")
    production_requirements = read("medichain/requirements-production.txt")
    render = read("render.yaml")
    start = read("medichain/backend/start.py")

    assert "genlayer@0.39.2" in dockerfile
    assert "USER medichain" in dockerfile
    assert 'CMD ["python", "start.py"]' in dockerfile
    assert "pytest" not in production_requirements
    assert "MEDICHAIN_BACKEND_MODE" in render
    assert "GENLAYER_RPC_URL" in render
    assert "sync: false" in render
    assert "numInstances: 1" in render
    assert "--limit-concurrency" in start
    assert "--no-server-header" in start


def assert_python_parses() -> None:
    for path in [
        "medichain/backend/config.py",
        "medichain/backend/auth_store.py",
        "medichain/backend/wallet_auth.py",
        "medichain/backend/genlayer_client.py",
        "medichain/backend/main.py",
        "medichain/backend/persistence.py",
        "medichain/backend/start.py",
    ]:
        ast.parse(read(path), filename=path)

    setup_script = read("medichain/backend/setup_genlayer_account.mjs")
    assert "wallet.encrypt(password)" in setup_script
    assert "private_key" in setup_script
    assert "process.argv" not in setup_script


def main() -> int:
    assert_python_parses()
    assert_backend_config()
    assert_frontend_config()
    assert_env_template()
    assert_deployment_config()
    print("Production readiness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
