from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


_LEVEL_MAP = {
    # beginner
    "basic": "beginner",
    "beginner": "beginner",
    "novice": "beginner",
    "foundation": "beginner",
    "foundational": "beginner",
    "familiar": "beginner",
    "awareness": "beginner",
    # intermediate
    "intermediate": "intermediate",
    "proficient": "intermediate",
    "practitioner": "intermediate",
    "experienced": "intermediate",
    # advanced
    "advanced": "advanced",
    "expert": "advanced",
    "authority": "advanced",
    "master": "advanced",
    "lead": "advanced",
}


# PUBLIC_INTERFACE
def normalize_level(level_raw: str) -> Optional[str]:
    """Maps freeform level strings into {'beginner','intermediate','advanced'}.

    Returns None when no suitable mapping is found.
    """
    if level_raw is None:
        return None
    s = str(level_raw).strip().lower()
    s = re.sub(r"[^a-z]+", " ", s).strip()
    if s in _LEVEL_MAP:
        return _LEVEL_MAP[s]

    # Handle single-letter (B/I/A) or numeral scales (1/2/3/4)
    # Any presence of words like 'basic', 'proficient' already handled.
    if s in {"b"}:
        return "beginner"
    if s in {"i", "p"}:
        return "intermediate"
    if s in {"a", "e", "m"}:
        return "advanced"

    # If numeric scale
    try:
        v = float(s)
        # map 1-4 scale (1=beginner, 2/3=intermediate, 4=advanced)
        if v <= 1.5:
            return "beginner"
        if v <= 3.0:
            return "intermediate"
        return "advanced"
    except Exception:
        pass

    return None


# PUBLIC_INTERFACE
def build_from_competency_mapping(
    mapping_df: pd.DataFrame, limit: Optional[int] = None
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Build seed rows for competencies, roles, and role_competencies from a long-form mapping DataFrame.

    Args:
      mapping_df: columns ['competency', 'role', 'level_raw']
      limit: Optional cap to reduce number of role_competency associations (for smoke tests)

    Returns:
      (competencies, roles, role_competencies) where:
        competencies -> [{'name': str}]
        roles -> [{'name': str}]
        role_competencies -> [{'role_name': str, 'competency_name': str, 'level': str}]
    """
    # Normalize levels
    mapping_df = mapping_df.copy()
    mapping_df["level"] = mapping_df["level_raw"].map(normalize_level)
    mapping_df = mapping_df[~mapping_df["level"].isna()]

    competencies = sorted(set(mapping_df["competency"].tolist()))
    roles = sorted(set(mapping_df["role"].tolist()))

    comp_rows = [{"name": c} for c in competencies]
    role_rows = [{"name": r} for r in roles]

    assoc_rows: List[Dict] = []
    for _, row in mapping_df.iterrows():
        assoc_rows.append(
            {
                "role_name": row["role"],
                "competency_name": row["competency"],
                "level": row["level"],
            }
        )
    if limit is not None and limit > 0:
        assoc_rows = assoc_rows[:limit]

    return comp_rows, role_rows, assoc_rows


# PUBLIC_INTERFACE
def build_roles_from_cards(
    role_cards: Iterable[Dict[str, str]],
    existing_roles: Optional[Iterable[str]] = None,
) -> List[Dict]:
    """Build 'roles' rows from parsed role-card text dictionaries.

    Ensures deduplication with existing role names if provided.

    Returns:
      [{'name': str, 'description': str, 'category': Optional[str]}]
    """
    seen = set(r for r in (existing_roles or []))
    rows: List[Dict] = []
    for rc in role_cards:
        name = (rc.get("name") or "").strip()
        if not name:
            continue
        if name in seen:
            # Augment description if provided and not empty
            continue
        seen.add(name)
        rows.append(
            {
                "name": name,
                "description": (rc.get("description") or "").strip(),
                "category": (rc.get("category") or "").strip() or None,
            }
        )
    return rows


def _parse_score(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value).strip()
    if s.endswith("%"):
        try:
            return float(s.rstrip("%").strip())
        except Exception:
            return None
    try:
        v = float(s)
        # If it's <= 1.0 treat as fraction; else percentage
        return v * 100.0 if v <= 1.0 else v
    except Exception:
        return None


# PUBLIC_INTERFACE
def build_role_adjacency_records(
    adjacency_df: pd.DataFrame, limit: Optional[int] = None
) -> List[Dict]:
    """Build 'role_adjacency' upsert rows using role names.

    Returns:
      [{'source_role_name': str, 'target_role_name': str, 'score': float}]
    """
    rows: List[Dict] = []
    for _, row in adjacency_df.iterrows():
        score = _parse_score(row.get("score_raw"))
        if score is None:
            continue
        src = (row.get("source_role") or "").strip()
        dst = (row.get("target_role") or "").strip()
        if not src or not dst or src == dst:
            continue
        rows.append({"source_role_name": src, "target_role_name": dst, "score": score})
    if limit is not None and limit > 0:
        rows = rows[:limit]
    return rows


# PUBLIC_INTERFACE
def attach_ids_for_relationships(
    role_competencies: List[Dict],
    role_name_to_id: Dict[str, int],
    competency_name_to_id: Dict[str, int],
) -> List[Dict]:
    """Convert role/competency name-based associations to id-based rows.

    Input:
      role_competencies: [{'role_name','competency_name','level'}]

    Output:
      [{'role_id': int, 'competency_id': int, 'level': str}]
    """
    out: List[Dict] = []
    for rc in role_competencies:
        rname = rc["role_name"]
        cname = rc["competency_name"]
        if rname not in role_name_to_id or cname not in competency_name_to_id:
            continue
        out.append(
            {
                "role_id": role_name_to_id[rname],
                "competency_id": competency_name_to_id[cname],
                "level": rc["level"],
            }
        )
    return out


# PUBLIC_INTERFACE
def attach_ids_for_role_adjacency(
    role_adjacency: List[Dict],
    role_name_to_id: Dict[str, int],
) -> List[Dict]:
    """Convert name-based adjacency to id-based rows.

    Input:
      [{'source_role_name','target_role_name','score'}]

    Output:
      [{'source_role_id': int, 'target_role_id': int, 'score': float}]
    """
    out: List[Dict] = []
    for ra in role_adjacency:
        sname = ra["source_role_name"]
        tname = ra["target_role_name"]
        if sname not in role_name_to_id or tname not in role_name_to_id:
            continue
        out.append(
            {
                "source_role_id": role_name_to_id[sname],
                "target_role_id": role_name_to_id[tname],
                "score": ra["score"],
            }
        )
    return out


# PUBLIC_INTERFACE
def build_learning_resources_from_navigator(
    navigator_df: Optional[pd.DataFrame], limit: Optional[int] = None
) -> List[Dict]:
    """Extract learning resources from a Role Navigator worksheet into name-based rows.

    Heuristics:
      - Identify URL-like cells anywhere in the sheet (http/https links).
      - Associate each URL with a competency when a competency-like column is present
        (e.g., 'competency', 'competencies', 'skill', 'capability').
      - Title is taken from a 'title'/'name'/'resource' column if present; otherwise fallback to URL.

    Returns:
      [{'competency_name': str, 'url': str, 'title': str}]

    Notes:
      - Rows without a recognizable competency name are skipped to avoid FK issues.
      - Duplicate (competency_name, url) pairs are de-duplicated.
    """
    if navigator_df is None or navigator_df.empty:
        return []

    # Normalize column name lookup
    cols_lower = {c.lower().strip(): c for c in navigator_df.columns}
    comp_col_candidates = ("competency", "competencies", "skill", "skills", "capability", "capabilities")
    title_col_candidates = ("title", "name", "resource", "learning", "description")

    comp_col: Optional[str] = next((cols_lower[c] for c in comp_col_candidates if c in cols_lower), None)
    title_col: Optional[str] = next((cols_lower[c] for c in title_col_candidates if c in cols_lower), None)

    url_pattern = re.compile(r"https?://\S+", re.IGNORECASE)
    out: List[Dict] = []
    seen: set[tuple[str, str]] = set()

    # Scan each row: gather all URL-like values across the row
    for _, row in navigator_df.iterrows():
        comp_name = (str(row[comp_col]).strip() if comp_col and pd.notna(row.get(comp_col)) else "")
        if not comp_name:
            # Without a competency, we skip to avoid orphan resources
            continue

        # Determine title fallback from a title-like column if present
        fallback_title = ""
        if title_col and pd.notna(row.get(title_col)):
            fallback_title = str(row.get(title_col)).strip()

        # Search all cells in the row for URLs
        urls: List[str] = []
        for val in row.tolist():
            if pd.isna(val):
                continue
            s = str(val)
            for m in url_pattern.findall(s):
                urls.append(m.strip())

        for u in urls:
            key = (comp_name, u)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "competency_name": comp_name,
                    "url": u,
                    "title": fallback_title or u,
                }
            )

    if limit is not None and limit > 0:
        out = out[:limit]
    return out
