import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

TESTNET_BASE_URL = "https://testnet.binance.vision/api"


def get_client() -> Client:
    environment = os.getenv("ENVIRONMENT", "production")

    if environment == "testnet":
        api_key = os.getenv("TESTNET_API_KEY")
        api_secret = os.getenv("TESTNET_SECRET")

        if not api_key or not api_secret:
            raise ValueError(
                "TESTNET_API_KEY and TESTNET_SECRET must be set in .env for testnet mode"
            )

        client = Client(api_key, api_secret, testnet=True)
        client.API_URL = TESTNET_BASE_URL
    else:
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_SECRET")

        if not api_key or not api_secret:
            raise ValueError(
                "BINANCE_API_KEY and BINANCE_SECRET must be set in .env for production mode"
            )

        client = Client(api_key, api_secret)

    return client
