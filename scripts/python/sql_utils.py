"""
Common SQL Server connection utilities for the Zoning & Slotting project.
Centralizes SQLAlchemy engine creation and driver selection.
"""

import sqlalchemy as sa
from sqlalchemy.engine import URL
from typing import Optional
from config.settings import AX_DATABASE, AX_DRIVERS, AX_SERVER

def get_ax_engine(
    server: str = AX_SERVER,
    database: str = AX_DATABASE,
    verbose: bool = False
) -> sa.Engine:
    """
    Creates and returns a SQLAlchemy Engine for AX Production.
    Tries multiple drivers and handles Windows authentication automatically.
    """
    last_error = None
    
    for drv in AX_DRIVERS:
        if verbose:
            print(f"      - Attempting AX connection with {drv}...")
            
        connection_url = URL.create(
            "mssql+pyodbc",
            host=server,
            database=database,
            query={
                "driver": drv,
                "trusted_connection": "yes",
                "TrustServerCertificate": "yes",
            },
        )
        
        # We use fast_executemany to speed up bulk inserts if needed later
        engine = sa.create_engine(
            connection_url,
            fast_executemany=True,
            # Adjust pooling for long-running scripts if necessary
            pool_pre_ping=True
        )
        
        try:
            # Simple health check
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            if verbose:
                print(f"      - Success: Connected to {server} using {drv}.")
            return engine
        except Exception as e:
            last_error = e
            continue
            
    raise RuntimeError(
        f"Could not connect to AX SQL Server '{server}' with any configured ODBC driver.\n"
        f"Tried: {AX_DRIVERS}\n"
        f"Last error: {last_error}"
    )

def execute_query(query: str, engine: Optional[sa.Engine] = None):
    """Simple wrapper to execute a query and return results."""
    if engine is None:
        engine = get_ax_engine()
        
    with engine.connect() as conn:
        return conn.execute(sa.text(query))
