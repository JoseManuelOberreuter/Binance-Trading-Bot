"""
Binance USDⓈ-M Futures client (testnet or production), mirroring the legacy spot
bot's exchange/client.py pattern.

Uses its own env var FUTURES_ENVIRONMENT (default: testnet) instead of the legacy
bot's ENVIRONMENT, so switching one system never switches the other. Production
must be requested explicitly. API keys are never logged or printed.
"""

from __future__ import annotations

import os

from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

TESTNET = "testnet"
PRODUCTION = "production"


def futures_environment() -> str:
    env = os.getenv("FUTURES_ENVIRONMENT", TESTNET).strip().lower()
    if env not in (TESTNET, PRODUCTION):
        raise ValueError(f"FUTURES_ENVIRONMENT must be '{TESTNET}' or '{PRODUCTION}', got {env!r}")
    return env


def get_futures_client(environment: str | None = None) -> Client:
    environment = environment or futures_environment()
    if environment == TESTNET:
        key_var, secret_var = "FUTURES_TESTNET_API_KEY", "FUTURES_TESTNET_SECRET"
    elif environment == PRODUCTION:
        key_var, secret_var = "BINANCE_API_KEY", "BINANCE_SECRET"
    else:
        raise ValueError(f"unknown environment {environment!r}")

    api_key, api_secret = os.getenv(key_var), os.getenv(secret_var)
    if not api_key or not api_secret:
        raise ValueError(f"{key_var} and {secret_var} must be set in .env for {environment} mode")

    # testnet=True makes python-binance route futures_* calls to testnet.binancefuture.com.
    return Client(api_key, api_secret, testnet=(environment == TESTNET))
