(function () {
  window.MEDICHAIN_CONFIG = Object.assign({
    API_BASE_URL: window.location.origin,
    WALLET_CHAIN_ID: 4221,
    WALLET_CHAIN_NAME: "GenLayer Bradbury",
    WALLET_RPC_URL: "https://rpc.testnet-chain.genlayer.com",
    WALLET_EXPLORER_URL: "https://explorer.testnet-chain.genlayer.com",
  }, window.MEDICHAIN_CONFIG || {});
}());
