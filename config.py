import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)


class ConfigError(Exception):
    """Не хватает ключей или они пустые."""


def get_spotify_credentials() -> tuple[str, str]:
    """Возвращает (client_id, client_secret). Бросает ConfigError, если их нет."""
    client_id = os.getenv("SPOTIPY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        raise ConfigError(
            f"Не найдены SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET.\n"
            f"Создай файл {ENV_PATH} и заполни его по образцу .env.example."
        )
    return client_id, client_secret


def has_spotify_credentials() -> bool:
    try:
        get_spotify_credentials()
        return True
    except ConfigError:
        return False