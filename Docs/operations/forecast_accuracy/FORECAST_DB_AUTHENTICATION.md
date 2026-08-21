# Corporate Forecast DB Authentication

## Default: cached Azure CLI token

Forecast DB Python tools use `scripts/python/forecast_db_auth.py`. The default
authentication mode is `AzureCLI`: `AzureCliCredential` requests an Azure SQL
access token from the local Azure CLI cache and passes it directly to ODBC via
`SQL_COPT_SS_ACCESS_TOKEN`.

The token is held in process memory only. It is not placed in a connection
string, environment variable, repository file, or log.

## One-Time Workstation Setup

Install the 64-bit Azure CLI:

```powershell
winget install --id Microsoft.AzureCLI --exact
```

Create the cached Hanna tenant session:

```powershell
az login `
  --tenant d977da7e-372a-4369-b692-487f0d0adbe2 `
  --allow-no-subscriptions
```

`--allow-no-subscriptions` is intentional. Azure SQL data access is granted
inside the database; a personal Azure subscription is not required for this
tenant-level login.

After the first login, normal commands should not open an account picker:

```powershell
uv run python scratch/profile_forecast_db_dates.py
uv run python scripts/python/forecast_corporate_db_extract.py --dry-run
```

If Conditional Access, MFA, a password change, or session revocation expires
the cached login, rerun the `az login` command. To remove the local session:

```powershell
az logout
az account clear
```

## Configuration

Defaults:

- server: `azprodfcast01.572f3811ca67.database.windows.net`
- database: `Forecast`
- tenant: `d977da7e-372a-4369-b692-487f0d0adbe2`
- authentication: `AzureCLI`

Production extractor overrides can be supplied with:

- `ZS_FORECAST_DB_SERVER`
- `ZS_FORECAST_DB_DATABASE`
- `ZS_FORECAST_DB_DRIVER`
- `ZS_FORECAST_DB_AUTH`
- `ZS_FORECAST_DB_TENANT_ID`
- `ZS_FORECAST_DB_USER` (used only by explicit ODBC authentication modes)

Interactive recovery remains available:

```powershell
uv run python scratch/profile_forecast_db_dates.py `
  --auth ActiveDirectoryInteractive
```

Do not use `ActiveDirectoryPassword` or store user credentials in `.env`.

## Validated 2026-08-21

- Azure CLI `2.89.1` installed.
- ODBC Driver 18.5.1 installed.
- Cached tenant login: `labreu@hannaandersson.com`.
- Two independent Python processes connected without a second prompt.
- SQL identity resolved to `labreu@hannaandersson.com` in database `Forecast`.

`ActiveDirectoryIntegrated` was tested and rejected for this workstation. The
tenant reports the account as managed rather than federated, so the ODBC
Windows Integrated Authentication Exchange requires interaction. Cached Azure
CLI token authentication is the supported local-development path here.
