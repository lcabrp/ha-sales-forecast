"""Shared Microsoft Entra authentication for the corporate Forecast database.

The default path acquires an Azure SQL access token from the locally cached
Azure CLI session and passes it directly to ODBC. Tokens remain in process
memory and are never written to connection strings, environment variables, or
repo files. Interactive ODBC authentication remains available as an explicit
fallback for recovery and first-time setup.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

import pyodbc
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AzureCliCredential, CredentialUnavailableError


DEFAULT_SERVER = "azprodfcast01.572f3811ca67.database.windows.net"
DEFAULT_DATABASE = "Forecast"
DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"
DEFAULT_AUTH = "AzureCLI"
DEFAULT_USER = "labreu@hannaandersson.com"
DEFAULT_TENANT_ID = "d977da7e-372a-4369-b692-487f0d0adbe2"
AZURE_SQL_SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256


def _ensure_azure_cli_on_path() -> str:
    """Return the Azure CLI executable, including the standard Windows install fallback."""
    executable = shutil.which("az")
    if executable:
        return executable

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft SDKs"
        / "Azure"
        / "CLI2"
        / "wbin"
        / "az.cmd",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft SDKs"
        / "Azure"
        / "CLI2"
        / "wbin"
        / "az.cmd",
    ]
    for candidate in candidates:
        if candidate.exists():
            os.environ["PATH"] = f"{candidate.parent}{os.pathsep}{os.environ.get('PATH', '')}"
            return str(candidate)
    raise FileNotFoundError(
        "Azure CLI was not found. Install it with: "
        "winget install --id Microsoft.AzureCLI --exact"
    )


def build_connection_string(
    *,
    server: str = DEFAULT_SERVER,
    database: str = DEFAULT_DATABASE,
    driver: str = DEFAULT_DRIVER,
    auth: str = DEFAULT_AUTH,
    user: str | None = DEFAULT_USER,
    timeout: int = 60,
) -> str:
    """Build a secure ODBC connection string for token or explicit auth."""
    server_value = server if server.lower().startswith("tcp:") else f"tcp:{server},1433"
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server_value}",
        f"DATABASE={database}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        f"Connection Timeout={timeout}",
    ]
    if auth.casefold() != "azurecli":
        parts.append(f"Authentication={auth}")
        if user:
            parts.append(f"UID={user}")
    return ";".join(parts)


def _azure_cli_token_struct(tenant_id: str, timeout: int) -> bytes:
    _ensure_azure_cli_on_path()
    credential = AzureCliCredential(
        tenant_id=tenant_id,
        process_timeout=max(timeout, 10),
    )
    try:
        token = credential.get_token(AZURE_SQL_SCOPE).token
    except (ClientAuthenticationError, CredentialUnavailableError) as exc:
        login_command = f"az login --tenant {tenant_id} --allow-no-subscriptions"
        raise RuntimeError(
            "No usable cached Azure CLI login was found. Run this once, then retry: "
            f"{login_command}"
        ) from exc
    token_bytes = token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def connect_forecast_db(
    *,
    server: str = DEFAULT_SERVER,
    database: str = DEFAULT_DATABASE,
    driver: str = DEFAULT_DRIVER,
    auth: str = DEFAULT_AUTH,
    user: str | None = DEFAULT_USER,
    tenant_id: str = DEFAULT_TENANT_ID,
    timeout: int = 60,
) -> pyodbc.Connection:
    """Open a Forecast DB connection using cached CLI or an explicit ODBC auth mode."""
    connection_string = build_connection_string(
        server=server,
        database=database,
        driver=driver,
        auth=auth,
        user=user,
        timeout=timeout,
    )
    if auth.casefold() == "azurecli":
        token_struct = _azure_cli_token_struct(tenant_id, timeout)
        return pyodbc.connect(
            connection_string,
            attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
        )
    return pyodbc.connect(connection_string)
