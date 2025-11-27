from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

import pandas as pd


def _safe_read_text(path: Union[str, Path]) -> str:
    """
    Reads a text file with best-effort encoding fallbacks.
    """
    path = Path(path)
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    # last resort: binary then decode ignoring errors
    return path.read_bytes().decode(errors="ignore")


# PUBLIC_INTERFACE
def load_competency_mapping_excel(xlsx_path: Union[str, Path]) -> pd.DataFrame:
    """Load the competency mapping workbook into a normalized long-form DataFrame.

    The expected structure (loosely enforced):
    - First column: competency name (e.g., 'Competency', 'Competencies', 'Skill', etc.)
    - Remaining columns: role names
    - Cells: level strings like 'basic', 'proficient', 'advanced', 'master' etc.

    Returns a DataFrame with columns:
      ['competency', 'role', 'level_raw']

    This function is resilient to minor header naming variations and extra empty columns/rows.
    """
    df = pd.read_excel(xlsx_path, engine="openpyxl")
    # Drop completely empty rows/cols
    df = df.dropna(how="all").dropna(axis=1, how="all")

    # Attempt to identify the competency column
    candidates = ["competency", "competencies", "skill", "skills", "capability", "capabilities"]
    colmap = {c.lower().strip(): c for c in df.columns}
    comp_col: Optional[str] = None
    for c in candidates:
        if c in colmap:
            comp_col = colmap[c]
            break
    if comp_col is None:
        # fallback to the first column as competency
        comp_col = df.columns[0]

    # Standardize column names
    df = df.rename(columns={comp_col: "competency"})
    # Melt to long format for roles/levels
    role_cols = [c for c in df.columns if c != "competency"]
    long_df = df.melt(id_vars=["competency"], value_vars=role_cols, var_name="role", value_name="level_raw")
    long_df["competency"] = long_df["competency"].astype(str).str.strip()
    long_df["role"] = long_df["role"].astype(str).str.strip()
    # Drop blank level cells
    long_df = long_df[~long_df["level_raw"].isna()].copy()
    # Normalize whitespace
    long_df["level_raw"] = long_df["level_raw"].astype(str).str.strip()
    # Filter rows without competency/role names
    long_df = long_df[(long_df["competency"] != "") & (long_df["role"] != "")]
    return long_df.reset_index(drop=True)


# PUBLIC_INTERFACE
def load_role_adjacency_excel(
    xlsx_paths: Iterable[Union[str, Path]]
) -> pd.DataFrame:
    """Load one or more role adjacency Excel files and return a normalized DataFrame.

    Supported shapes:
    1) Matrix form:
       - First column is role names (source)
       - Remaining columns are role names (targets)
       - Cell is a score or percentage (e.g., '40%', 40, 0.4)

    2) Long form:
       - Columns like ['source', 'target', 'score'] or variants
         (e.g., 'from', 'to', 'match', 'adjacency', 'percentage')

    Returns a concatenated DataFrame with columns:
      ['source_role', 'target_role', 'score_raw']
    """
    frames: List[pd.DataFrame] = []
    for path in xlsx_paths:
        df = pd.read_excel(path, engine="openpyxl")
        df = df.dropna(how="all").dropna(axis=1, how="all")
        # Try to detect long-form columns
        cols_lower = {c.lower().strip(): c for c in df.columns}
        source_col = None
        target_col = None
        score_col = None
        for candidate in ("source", "from", "role", "current", "src"):
            if candidate in cols_lower:
                source_col = cols_lower[candidate]
                break
        for candidate in ("target", "to", "next", "dst", "role_2"):
            if candidate in cols_lower:
                target_col = cols_lower[candidate]
                break
        for candidate in ("score", "adjacency", "match", "percentage", "similarity"):
            if candidate in cols_lower:
                score_col = cols_lower[candidate]
                break

        if source_col and target_col and score_col:
            tmp = df[[source_col, target_col, score_col]].copy()
            tmp.columns = ["source_role", "target_role", "score_raw"]
            frames.append(tmp)
            continue

        # Treat as a matrix if the above didn't match
        # First column should be the source role
        src_col = df.columns[0]
        role_matrix = df.melt(id_vars=[src_col], var_name="target_role", value_name="score_raw")
        role_matrix = role_matrix.rename(columns={src_col: "source_role"})
        frames.append(role_matrix)

    if not frames:
        return pd.DataFrame(columns=["source_role", "target_role", "score_raw"])

    out = pd.concat(frames, ignore_index=True)
    # Clean names and drop self-links or empty
    out["source_role"] = out["source_role"].astype(str).str.strip()
    out["target_role"] = out["target_role"].astype(str).str.strip()
    out = out[(out["source_role"] != "") & (out["target_role"] != "")]
    # Drop rows where score is NA or empty
    out = out[~out["score_raw"].isna()]
    return out.reset_index(drop=True)


# PUBLIC_INTERFACE
def load_role_card_texts(paths: Iterable[Union[str, Path]]) -> List[Dict[str, str]]:
    """Parse role-card text files into structured role dictionaries.

    Heuristics:
    - Role name: Prefer the first non-empty line; fallback to a cleaned name from filename.
    - Description: Use the first paragraph containing 'Mission' or the first 3–5 lines as summary.

    Returns:
      List of dicts: [{'name': str, 'description': str, 'category': Optional[str]}]
    """
    roles: List[Dict[str, str]] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        content = _safe_read_text(p)
        lines = [ln.strip() for ln in content.splitlines()]
        # Identify the first non-empty line as name
        name = next((ln for ln in lines if ln), "")
        if not name:
            # fallback to filename derived
            name = p.stem
        # Clean up noisy prefixes like "The <Role> Role"
        # e.g., "The Chief Architect Role" -> "Chief Architect"
        name = re.sub(r"^The\s+", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+Role$", "", name, flags=re.IGNORECASE)

        # Find a description block
        desc = ""
        # Prefer a section starting at "Mission", else take first few non-empty lines
        try:
            idx = next(i for i, ln in enumerate(lines) if ln.lower().startswith("mission"))
            # Take mission and following lines until a blank line
            chunk: List[str] = []
            for ln in lines[idx : min(idx + 12, len(lines))]:
                if ln == "":
                    break
                chunk.append(ln)
            desc = " ".join(chunk)
        except StopIteration:
            # Fallback to the first 5 non-empty lines
            chunk = [ln for ln in lines if ln][:5]
            desc = " ".join(chunk)

        # Category heuristic from file name (e.g., 'Role_Card_AppDev_v3' -> 'AppDev')
        category = None
        m = re.search(r"Role[_\s]Card[_\s]([A-Za-z0-9]+)", p.stem, flags=re.IGNORECASE)
        if m:
            category = m.group(1)

        roles.append({"name": name.strip(), "description": desc.strip(), "category": (category or "").strip()})
    return roles


# PUBLIC_INTERFACE
def load_role_navigator_excel(xlsx_path: Union[str, Path]) -> pd.DataFrame:
    """Load the Role Navigator worksheet.

    Because formats often vary, this returns the raw first sheet as-is, after
    dropping completely empty rows and columns. The transform layer will
    decide how (or whether) to use it (e.g., learning resources).
    """
    df = pd.read_excel(xlsx_path, engine="openpyxl")
    df = df.dropna(how="all").dropna(axis=1, how="all")
    return df.reset_index(drop=True)
