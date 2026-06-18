from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class Config:
    wordpress_base_url: str
    wordpress_username: str | None
    wordpress_app_password: str | None
    lastfm_api_key: str | None
    batch_size: int
    apply: bool
    limit: int | None
    timeout_s: float


def load_config(argv: list[str]) -> Config:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    base_url = os.getenv("WORDPRESS_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("WORDPRESS_BASE_URL is required")
    return Config(
        wordpress_base_url=base_url,
        wordpress_username=os.getenv("WORDPRESS_USERNAME"),
        wordpress_app_password=os.getenv("WORDPRESS_APP_PASSWORD"),
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        batch_size=args.batch_size,
        apply=args.apply,
        limit=args.limit,
        timeout_s=args.timeout,
    )
