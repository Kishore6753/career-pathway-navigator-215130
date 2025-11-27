from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from supabase import Client, create_client  # type: ignore


# PUBLIC_INTERFACE
def get_supabase_client() -> Client:
    """Create a Supabase client using environment variables.

    Required envs:
      - SUPABASE_URL
      - SUPABASE_SERVICE_ROLE_KEY

    Note:
      Service role key must be used for privileged operations (RLS bypass and truncation).
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment.")
    return create_client(url, key)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
)
def _upsert_chunk(
    client: Client,
    table: str,
    rows: List[Dict],
    on_conflict: str,
    returning: str = "minimal",
) -> int:
    """Upsert a chunk of rows and return affected count (best-effort)."""
    if not rows:
        return 0
    client.table(table).upsert(rows, on_conflict=on_conflict, returning=returning).execute()
    # When returning='minimal', .data may be empty; we can't get exact count reliably.
    # Prefer to use length of rows as processed count.
    processed = len(rows)
    return processed


# PUBLIC_INTERFACE
def upsert_in_batches(
    client: Client,
    table: str,
    rows: Iterable[Dict],
    on_conflict: str,
    batch_size: int = 500,
    returning: str = "minimal",
    verbose: bool = True,
) -> int:
    """Upsert rows in manageable batches with retry/backoff.

    Args:
      client: Supabase client
      table: table name
      rows: iterable of dictionaries
      on_conflict: column or comma-separated columns that form a unique key
      batch_size: number of rows per upsert chunk
      returning: 'minimal' or 'representation' (minimal for performance)
      verbose: print progress info

    Returns:
      Total number of rows processed (not strictly equal to inserted/updated).
    """
    buf: List[Dict] = []
    total = 0
    for row in rows:
        buf.append(row)
        if len(buf) >= batch_size:
            total += _upsert_chunk(client, table, buf, on_conflict, returning=returning)
            if verbose:
                print(f"[{table}] processed: {total}")
            buf = []
    if buf:
        total += _upsert_chunk(client, table, buf, on_conflict, returning=returning)
        if verbose:
            print(f"[{table}] processed: {total}")
    return total


# PUBLIC_INTERFACE
def delete_all(client: Client, table: str) -> int:
    """Dangerous: delete all rows from a table (service role key required).

    Returns:
      Number of rows deleted if known (best-effort), else 0.
    """
    # We try to get count by selecting first, then delete.
    cnt = get_count(client, table)
    # Delete every row by a tautological filter (id >= 0)
    client.table(table).delete().gte("id", 0).execute()
    return cnt or 0


# PUBLIC_INTERFACE
def get_count(client: Client, table: str) -> Optional[int]:
    """Return exact row count for a table."""
    resp = client.table(table).select("id", count="exact").limit(1).execute()
    # supabase-py v2: count is on resp.count
    return getattr(resp, "count", None)


# PUBLIC_INTERFACE
def get_id_map(client: Client, table: str, key_field: str = "name", id_field: str = "id") -> Dict[str, int]:
    """Fetch a mapping of key_field -> id for a reference table."""
    # Retrieve in pages to avoid huge payloads
    page = 0
    limit = 1000
    out: Dict[str, int] = {}
    while True:
        start = page * limit
        end = start + limit - 1
        resp = client.table(table).select(f"{id_field},{key_field}").range(start, end).execute()
        data = resp.data or []
        if not data:
            break
        for row in data:
            key = str(row[key_field]).strip()
            out[key] = int(row[id_field])
        if len(data) < limit:
            break
        page += 1
    return out
