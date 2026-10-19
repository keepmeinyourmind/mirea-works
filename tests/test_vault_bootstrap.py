import pytest

from study_bot.config import Settings
from study_bot.db import DB
from study_bot.main import bootstrap_admin
from study_bot.vault import Vault, new_key


def test_vault_roundtrip_and_wrong_key():
    v = Vault(new_key())
    token = v.encrypt("abc123")
    assert "abc123" not in token and v.decrypt(token) == "abc123"
    assert Vault(new_key()).decrypt(token) is None  # ключ сменили — просто попросим /cookie заново
    assert v.decrypt(None) is None


def test_vault_requires_key():
    with pytest.raises(RuntimeError, match="COOKIE_KEY"):
        Vault("")


def test_first_start_makes_admin_and_moves_legacy_cookie(tmp_path):
    s = Settings(_env_file=None, bot_token="1:x", owner_id=777, data_dir=tmp_path)
    s.legacy_cookie_path.write_text("old-cookie\n")
    db, v = DB(":memory:"), Vault(new_key())
    bootstrap_admin(s, db, v)
    assert v.decrypt(db.get_user(777).cookie_enc) == "old-cookie"
    assert not s.legacy_cookie_path.exists()  # открытый файл с cookie удалён
    bootstrap_admin(s, db, v)  # повторный запуск ничего не ломает
    assert db.get_user(777).name == "админ"
