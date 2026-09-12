"""Extracted shared tracker rules."""

import json

from kodezart.core.config import AppConfig


def _from_source(source, field, value, tmp_path, monkeypatch):
    name = "KODEZART_" + field.upper()
    if source == "init":
        return AppConfig(_env_file=None, **{field: value})
    encoded = value if isinstance(value, str) else json.dumps(value)
    if source == "env":
        monkeypatch.setenv(name, encoded)
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(f"{name}={encoded}\n")
        return AppConfig(_env_file=path)
    (tmp_path / name).write_text(encoded)
    return AppConfig(_env_file=None, _secrets_dir=tmp_path)
