from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

_UNSET = object()

# Scopes de SOLO LECTURA. Ninguno permite iniciar pagos ni transferencias.
SCOPES = [
    "info",
    "accounts",
    "balance",
    "transactions",
    "direct_debits",
    "standing_orders",
    "offline_access",  # necesario para obtener refresh_token
]

_BANK_ENV_RE = re.compile(r"^FINMCP_BANK_(\d+)_([A-Z0-9_]+)$")


@dataclass
class BankConfig:
    """Un banco (ASPSP) vinculado vía Enable Banking."""

    id: str
    aspsp_name: str = ""
    country: str = "ES"


def _discover_bank_configs(env_file: Path | None) -> list[BankConfig]:
    """Agrupa `FINMCP_BANK_<n>_<campo>` de .env/entorno en `BankConfig`s ordenados por n."""
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update(os.environ)  # el entorno real gana sobre .env, como pydantic-settings

    grouped: dict[int, dict[str, str]] = {}
    for key, val in values.items():
        m = _BANK_ENV_RE.match(key)
        if not m:
            continue
        idx, field_name = int(m.group(1)), m.group(2).lower()
        grouped.setdefault(idx, {})[field_name] = val

    return [
        BankConfig(
            id=grouped[idx].get("id", str(idx)),
            aspsp_name=grouped[idx].get("aspsp_name", ""),
            country=grouped[idx].get("country", "ES"),
        )
        for idx in sorted(grouped)
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ruta del .env efectivo; los bancos se descubren fuera de pydantic y necesitan
    # respetar el `_env_file` recibido (los tests pasan None para aislarse del .env real).
    _env_file_path: Path | None = PrivateAttr(default=None)

    # --- Enable Banking ---
    enablebanking_app_id: str = ""  # Application ID (kid del JWT)
    enablebanking_key_path: Path | None = None  # ruta al .pem de la clave privada
    enablebanking_aspsp_name: str = ""  # nombre exacto del banco, p.ej. CaixaBank
    enablebanking_country: str = "ES"
    # Enable Banking exige HTTPS; usamos copy-paste del code (no se captura por servidor).
    enablebanking_redirect_uri: str = "https://localhost:3000/callback"

    # --- Almacenamiento ---
    finmcp_db_path: Path | None = None

    # --- Seguridad ---
    finmcp_encryption_key: str = ""
    finmcp_callback_port: int = 3000

    def __init__(self, **kwargs) -> None:
        env_file = kwargs.get("_env_file", _UNSET)
        super().__init__(**kwargs)
        if env_file is _UNSET:
            self._env_file_path = Path(__file__).resolve().parents[2] / ".env"
        elif env_file is not None:
            self._env_file_path = Path(env_file)

    @property
    def data_dir(self) -> Path:
        d = Path(__file__).resolve().parents[2] / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.finmcp_db_path or (self.data_dir / "finmcp.db")

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def scopes(self) -> list[str]:
        return SCOPES

    @property
    def enablebanking_base(self) -> str:
        return "https://api.enablebanking.com"

    @property
    def enablebanking_private_key(self) -> Path:
        return self.enablebanking_key_path or (
            self.data_dir / "enablebanking_private.pem"
        )

    @property
    def banks(self) -> list[BankConfig]:
        """Bancos configurados: `FINMCP_BANK_N_*` indexados, o el legacy de uno solo."""
        discovered = _discover_bank_configs(self._env_file_path)
        if discovered:
            return discovered
        if self.enablebanking_aspsp_name:
            return [
                BankConfig(
                    id="default",
                    aspsp_name=self.enablebanking_aspsp_name,
                    country=self.enablebanking_country,
                )
            ]
        return []


settings = Settings()
