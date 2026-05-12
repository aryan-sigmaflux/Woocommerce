"""
Application configuration via pydantic BaseSettings.
All values are loaded from .env at startup.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # WooCommerce
    WOO_BASE_URL: str
    WOO_CONSUMER_KEY: str
    WOO_CONSUMER_SECRET: str
    WOO_WEBHOOK_SECRET: str

    # Meta / WhatsApp Cloud API
    META_PHONE_NUMBER_ID: str
    META_ACCESS_TOKEN: str
    META_VERIFY_TOKEN: str
    META_CATALOG_ID: str
    META_API_VERSION: str = "v19.0"
    WA_FLOW_ID: str | None = None

    # Razorpay
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: str
    DUMMY_RAZORPAY: bool = False

    # Database
    DATABASE_URL: str

    # App
    APP_BASE_URL: str
    SESSION_EXPIRY_HOURS: int = 24
    PAYMENT_LINK_EXPIRY_MINUTES: int = 30
    MAX_COUPON_ATTEMPTS: int = 3

    # Shipping
    SHIPPING_FLAT_AMOUNT: float = 30.0

    # Region
    CURRENCY: str = "INR"
    COUNTRY: str = "IN"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
