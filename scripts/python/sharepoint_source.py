"""sharepoint_source.py — SharePoint Online Source File Downloader.

Downloads the weekly/daily 'Product Info for BRG.xlsx' directly from SharePoint Online,
replacing the manual download step in the legacy workflow.

The legacy Excel-based tools (Case Quantity Calcs, Active Storage Tool)
connected to this same file via OLE DB / Power Query using "Organizational
Account" authentication. This script replicates that exact auth flow using
Microsoft's MSAL library (the same identity stack that Power Query uses).

SharePoint file location (OneDrive for Business, owned by svc-az-pbi):
  https://hannacorp-my.sharepoint.com/personal/svc-az-pbi_hannaandersson_com/
  Documents/Product Info for BRG.xlsx

Authentication — "Organizational Account" flow:
  Power Query's "Organizational Account" = Azure AD OAuth2 with interactive
  browser sign-in. We replicate this with MSAL's acquire_token_interactive():

  1st run  → Browser opens → sign in with your org account (MFA supported)
  2nd+ run → MSAL silently refreshes the cached token — no browser, no prompt
  ~90 days → Refresh token expires → browser opens once more

  Token cache is persisted to disk (.auth/token_cache.bin) and encrypted via
  Windows DPAPI when msal-extensions is available.

Client ID:
  By default, this script uses Microsoft Office's well-known public client ID.
  If your tenant's conditional access policies block it, register a custom app
  in Azure AD (see CUSTOM_APP_REGISTRATION_GUIDE below) and set CLIENT_ID.

Usage:
  Called by ingestion_pipeline.py via get_source_file(). Standalone:
    uv run python scripts/python/sharepoint_source.py
    uv run python scripts/python/sharepoint_source.py --force
    uv run python scripts/python/sharepoint_source.py --clear-creds
"""

import argparse
import atexit
import glob
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# ══════════════════════════════════════════════════════════════════════════════
# Project Paths
# ══════════════════════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SOURCE_DIR = PROJECT_ROOT / "Source"

# ══════════════════════════════════════════════════════════════════════════════
# SharePoint Configuration
# ══════════════════════════════════════════════════════════════════════════════

# The host and site path identify the OneDrive for Business personal site
# where the source file lives. This is the same location that the legacy
# Power Query connections pointed to.
SHAREPOINT_HOST = "hannacorp-my.sharepoint.com"
SITE_PATH = "/personal/svc-az-pbi_hannaandersson_com"
FILE_NAME = "Product Info for BRG.xlsx"

# SharePoint REST API endpoint for direct file download. We use
# GetFileByServerRelativeUrl()/$value which returns the raw bytes.
# This avoids Microsoft Graph entirely (and the AADSTS65002 error).
SHAREPOINT_FILE_URL = (
    f"https://{SHAREPOINT_HOST}{SITE_PATH}"
    f"/_api/web/GetFileByServerRelativeUrl("
    f"'{quote(SITE_PATH + '/Documents/' + FILE_NAME)}')"
    f"/$value"
)

# ══════════════════════════════════════════════════════════════════════════════
# MSAL / Azure AD Configuration
#
# "Organizational Account" in Power Query = Azure AD OAuth2 with delegated
# permissions. MSAL is Microsoft's official library for this flow.
# ══════════════════════════════════════════════════════════════════════════════

# Microsoft Office's well-known public client ID. This is the same app
# identity that Word, Excel, and PowerPoint use for Azure AD auth. Using it
# means the user gets the same sign-in experience as opening a SharePoint
# file in Excel — no app registration needed.
#
# If your tenant blocks this client via Conditional Access, register your own
# app (see CUSTOM_APP_REGISTRATION_GUIDE below) and replace this value.
CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"

# "organizations" authority allows any Azure AD tenant. MSAL discovers the
# correct tenant from the user's email during interactive sign-in.
AUTHORITY = "https://login.microsoftonline.com/organizations"

# Scope targets SharePoint directly (not Microsoft Graph). The Office client
# ID is preauthorized for SharePoint in most tenants, but NOT for Graph —
# requesting Graph scopes triggers AADSTS65002. By scoping to the SharePoint
# host, we sidestep that entirely and talk to the same API that Power Query uses.
SCOPES = [f"https://{SHAREPOINT_HOST}/AllSites.Read"]

# ── Custom App Registration Guide ─────────────────────────────────────────
# If the default Office client ID doesn't work (Conditional Access block,
# admin consent required, etc.), register your own app:
#
# 1. Go to https://entra.microsoft.com → App registrations → New registration
# 2. Name: "Zoning Slotting Pipeline" (or anything descriptive)
# 3. Supported account types: "Single tenant" (your org only)
# 4. Redirect URI: select "Mobile and desktop" → http://localhost
# 5. After creation, note the Application (client) ID → set CLIENT_ID above
# 6. Under API permissions → Add → SharePoint → Delegated:
#    - AllSites.Read
# 7. Click "Grant admin consent for [your org]" (requires admin)
# ──────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# Token Cache
#
# MSAL stores OAuth tokens (access + refresh) in a serializable cache.
# We persist this to disk so the user only authenticates once. The refresh
# token (~90 day lifetime) lets MSAL silently renew access tokens on
# subsequent runs without opening a browser.
#
# On Windows, msal-extensions encrypts the cache file via DPAPI (same
# protection Windows Credential Manager uses). On other platforms, it
# falls back to plaintext — acceptable for a local dev tool, not for servers.
# ══════════════════════════════════════════════════════════════════════════════
AUTH_DIR = PROJECT_ROOT / ".auth"
TOKEN_CACHE_FILE = AUTH_DIR / "token_cache.bin"


def _build_msal_app():
    """Build MSAL public client application with persistent, optionally encrypted token cache.

    Tries to import `msal_extensions` to get OS-level data protection (DPAPI on Windows).
    If extensions are not available, falls back to a plain text serializable token cache on disk.

    Returns:
        msal.PublicClientApplication: An initialized MSAL public client.
    """
    import msal

    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = str(TOKEN_CACHE_FILE)

    # Prefer msal-extensions for OS-level encryption (Windows DPAPI).
    try:
        from msal_extensions import (
            FilePersistence,
            FilePersistenceWithDataProtection,
            PersistedTokenCache,
        )

        if sys.platform.startswith("win"):
            persistence = FilePersistenceWithDataProtection(cache_path)
        else:
            persistence = FilePersistence(cache_path)
        cache = PersistedTokenCache(persistence)

    except ImportError:
        # Fallback: unencrypted file cache. Functional for a local CLI tool.
        cache = msal.SerializableTokenCache()
        if os.path.exists(cache_path):
            with open(cache_path, "r") as f:
                cache.deserialize(f.read())
        # Automatically save cache state on exit
        atexit.register(
            lambda: open(cache_path, "w").write(cache.serialize())
            if cache.has_state_changed
            else None
        )

    return msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=cache
    )


def _acquire_token() -> str:
    """Acquire a Microsoft SharePoint Online access token.

    Mirrors Power Query's "Organizational Account" flow:
    1. Try silent renewal (cached refresh token).
    2. If no cached token or refresh expired, open browser for interactive login.

    Returns:
        str: Access token for SharePoint API calls.

    Raises:
        RuntimeError: If authentication fails.
    """
    app = _build_msal_app()

    # Silent first — succeeds when we have a valid refresh token
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print(f"[*] Authenticated silently as: {accounts[0]['username']}")
            return result["access_token"]

    # No cached token → interactive browser sign-in
    print("[*] Opening browser for SharePoint sign-in...")
    print("    (Same as 'Organizational Account' in Power Query)")
    result = app.acquire_token_interactive(scopes=SCOPES)

    if "access_token" in result:
        accounts = app.get_accounts()
        who = accounts[0]["username"] if accounts else "unknown"
        print(f"    Signed in as: {who}")
        return result["access_token"]

    error = result.get("error", "unknown_error")
    desc = result.get("error_description", "No details.")
    raise RuntimeError(f"Authentication failed: {error}\n    {desc}")


# ══════════════════════════════════════════════════════════════════════════════
# Download
# ══════════════════════════════════════════════════════════════════════════════

def _today_filename() -> str:
    """Generate date-stamped name matching the existing Source/ naming convention.

    Returns:
        str: Formatted filename e.g. "Product Info for BRG_MM-DD-YYYY.xlsx".
    """
    return f"Product Info for BRG_{datetime.now().strftime('%m-%d-%Y')}.xlsx"


def _todays_file_exists(dest_dir: Path) -> Path | None:
    """Check if the date-stamped file for today already exists in the destination folder.

    Args:
        dest_dir: Destination folder path.

    Returns:
        Path or None: Path to file if it exists, otherwise None.
    """
    target = dest_dir / _today_filename()
    return target if target.exists() else None


def download_from_sharepoint(
    dest_dir: Path, force: bool = False
) -> Path | None:
    """Download the source file via SharePoint REST API.

    Uses the /_api/web/GetFileByServerRelativeUrl()/$value endpoint, which
    returns raw file bytes directly. This targets the SharePoint resource
    (not Microsoft Graph), avoiding the AADSTS65002 preauthorization error
    that blocks first-party client IDs from accessing Graph.

    Args:
        dest_dir: Path to save the downloaded workbook.
        force: If True, re-downloads even if today's file already exists.

    Returns:
        Path or None: The Path to the saved file if successful, or None if failed.
    """
    import requests

    if not force:
        existing = _todays_file_exists(dest_dir)
        if existing:
            print(f"[*] Today's file already exists: {existing.name}")
            print("    Skipping download. Use --force-download to re-download.")
            return existing

    token = _acquire_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("[*] Downloading from SharePoint...")
    print(f"    {SITE_PATH}/Documents/{FILE_NAME}")

    # Stream the download — the file is ~55 MB
    resp = requests.get(SHAREPOINT_FILE_URL, headers=headers, stream=True, timeout=300)

    if resp.status_code == 401:
        print("[!] Token rejected (401). Clearing cache — re-run to sign in again.")
        clear_cached_credentials()
        return None
    if resp.status_code == 403:
        print("[!] Access denied (403). Your account may lack permissions to this file.")
        return None
    if resp.status_code == 404:
        print("[!] File not found (404). Check SITE_PATH and FILE_NAME in the config.")
        return None

    resp.raise_for_status()

    # Write to temp file first, then rename — prevents partial files in Source/
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / _today_filename()

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=str(dest_dir))
    try:
        total = 0
        with os.fdopen(tmp_fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                total += len(chunk)

        # Sanity check file size (should be roughly ~55 MB, definitely > 1 MB)
        if total < 1_000_000:
            print(f"[!] Only {total:,} bytes received (expected ~55 MB). Aborting.")
            os.unlink(tmp_path)
            return None

        if dest_path.exists():
            dest_path.unlink()
        os.rename(tmp_path, dest_path)

        print(f"    Saved: {dest_path.name} ({total / (1024 * 1024):.1f} MB)")
        return dest_path

    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ══════════════════════════════════════════════════════════════════════════════
# Credential Management
# ══════════════════════════════════════════════════════════════════════════════

def clear_cached_credentials() -> None:
    """Delete the persisted token cache so the next run re-opens the browser."""
    if TOKEN_CACHE_FILE.exists():
        TOKEN_CACHE_FILE.unlink()
        print(f"Cleared token cache: {TOKEN_CACHE_FILE}")
    else:
        print("No cached tokens found — nothing to clear.")


# ══════════════════════════════════════════════════════════════════════════════
# Public API — Called by ingestion_pipeline.py
# ══════════════════════════════════════════════════════════════════════════════

def get_latest_local_file(source_dir: Path) -> Path | None:
    """Find the most recently modified source file in the Source directory.

    Args:
        source_dir: Folder to search.

    Returns:
        Path or None: Path to the latest file matching the pattern, or None.
    """
    pattern = str(source_dir / "Product Info for BRG*.xlsx")
    files = glob.glob(pattern)
    return Path(max(files, key=os.path.getmtime)) if files else None


def get_source_file(source_dir: Path, force_download: bool = False) -> Path:
    """Primary entry point. Tries SharePoint first, falls back to local file.

    Called by ingestion_pipeline.py to replace the original
    get_latest_source_file() with a two-step strategy:
      1. Download from SharePoint via REST API (skip if today's file exists).
      2. If download fails for any reason, use the newest local file in Source/.

    Args:
        source_dir: Directory where workbooks are saved.
        force_download: Force download from SharePoint.

    Returns:
        Path: Path to the downloaded or fallback file.

    Raises:
        FileNotFoundError: If SharePoint fails and no local fallbacks exist.
    """
    try:
        result = download_from_sharepoint(source_dir, force=force_download)
        if result:
            return result
    except Exception as e:
        print(f"\n[!] SharePoint download failed: {e}")

    # ── Fallback to local file ─────────────────────────────────────────────
    print()
    print("    ╔══════════════════════════════════════════════════════════╗")
    print("    ║  WARNING: Using local file — it may be outdated.         ║")
    print("    ║  The pipeline will continue, but verify the results      ║")
    print("    ║  against the current SharePoint data when possible.      ║")
    print("    ╚══════════════════════════════════════════════════════════╝")
    print()

    local_file = get_latest_local_file(source_dir)
    if local_file:
        mod_time = datetime.fromtimestamp(local_file.stat().st_mtime)
        days_old = (datetime.now() - mod_time).days
        print(f"[*] Falling back to local file: {local_file.name}")
        print(
            f"    Last modified: {mod_time.strftime('%Y-%m-%d %H:%M')} "
            f"({days_old} day{'s' if days_old != 1 else ''} ago)"
        )
        return local_file

    raise FileNotFoundError(
        "No source file available.\n"
        "  - SharePoint download failed (see errors above).\n"
        f"  - No local files matching 'Product Info for BRG*.xlsx' in {source_dir}\n"
        "\n"
        "To fix:\n"
        "  1. Check your network/VPN connection and try again, or\n"
        "  2. Manually download the file from SharePoint into Source/"
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """Main CLI entry point for sharepoint downloader script."""
    parser = argparse.ArgumentParser(
        description="Download 'Product Info for BRG.xlsx' from SharePoint Online."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if today's file already exists.",
    )
    parser.add_argument(
        "--clear-creds",
        action="store_true",
        help="Clear cached OAuth tokens and exit.",
    )
    args = parser.parse_args()

    if args.clear_creds:
        clear_cached_credentials()
        return

    print("=" * 60)
    print("  SharePoint Source File Downloader")
    print("=" * 60)

    try:
        result = get_source_file(SOURCE_DIR, force_download=args.force)
        print(f"\n[*] Source file ready: {result}")
    except FileNotFoundError as e:
        print(f"\n[!] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
