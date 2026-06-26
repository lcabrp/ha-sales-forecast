"""Common SQL Server connection utilities for the Zoning & Slotting project.

Centralizes SQLAlchemy engine creation, automatic driver negotiation,
Windows authentication, and execution helpers for Dynamics AX connection.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import URL
from typing import Optional
from config.settings import AX_DATABASE, AX_DRIVERS, AX_SERVER


def get_ax_engine(
    server: str = AX_SERVER,
    database: str = AX_DATABASE,
    verbose: bool = False
) -> sa.Engine:
    """Creates and returns a SQLAlchemy Engine for Dynamics AX Production database.

    Tries multiple configured ODBC drivers sequentially, and configures Windows
    integrated authentication and server certificate trust automatically.

    Args:
        server: SQL Server host name. Defaults to the configured AX_SERVER.
        database: Database name. Defaults to the configured AX_DATABASE.
        verbose: If True, prints logs during connection attempts.

    Returns:
        A SQLAlchemy Engine ready to connect.

    Raises:
        RuntimeError: If connection cannot be established using any of the
          configured ODBC drivers.
    """
    last_error = None
    
    for drv in AX_DRIVERS:
        if verbose:
            print(f"      - Attempting AX connection with {drv}...")
            
        # Build the SQLAlchemy connection URL for Microsoft SQL Server via pyodbc
        connection_url = URL.create(
            "mssql+pyodbc",
            host=server,
            database=database,
            query={
                "driver": drv,
                "trusted_connection": "yes",  # Use integrated Windows authentication
                "TrustServerCertificate": "yes",  # Allow self-signed or internal CA certs
            },
        )
        
        # fast_executemany speeds up bulk inserts dramatically by sending parameters in batches
        # pool_pre_ping checks connections on checkout to avoid stale socket errors in long-running jobs
        engine = sa.create_engine(
            connection_url,
            fast_executemany=True,
            pool_pre_ping=True
        )
        
        try:
            # Simple health check to verify connection validity
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            if verbose:
                print(f"      - Success: Connected to {server} using {drv}.")
            return engine
        except Exception as e:
            last_error = e
            continue
            
    # Raise custom runtime error with full diagnostics if no driver succeeded
    raise RuntimeError(
        f"Could not connect to AX SQL Server '{server}' with any configured ODBC driver.\n"
        f"Tried: {AX_DRIVERS}\n"
        f"Last error: {last_error}"
    )


def execute_query(query: str, engine: Optional[sa.Engine] = None):
    """Simple wrapper to execute a raw SQL query and return the execution results.

    Useful for one-off reads, metadata queries, and light operational checks.

    Args:
        query: Raw SQL query string to run.
        engine: Optional SQLAlchemy Engine. If not provided, a default engine
          is initialized using get_ax_engine().

    Returns:
        SQLAlchemy CursorResult.
    """
    if engine is None:
        engine = get_ax_engine()
        
    with engine.connect() as conn:
        return conn.execute(sa.text(query))
