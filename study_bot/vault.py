"""Шифрование cookie СДО в базе (Fernet: AES + HMAC). Ключ — COOKIE_KEY в .env, в базе его нет:
утечёт только файл базы — сессии из него не достать."""

from cryptography.fernet import Fernet, InvalidToken


def new_key() -> str:
    return Fernet.generate_key().decode()


class Vault:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError(
                "Не задан COOKIE_KEY. Сгенерируй: uv run python -c 'from study_bot.vault import new_key; print(new_key())'"
                " и впиши в .env строкой COOKIE_KEY=…"
            )
        self.f = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self.f.encrypt(value.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        """None — cookie нет или ключ сменился (тогда пользователю придётся прислать /cookie заново)."""
        if not token:
            return None
        try:
            return self.f.decrypt(token.encode()).decode()
        except InvalidToken:
            return None
