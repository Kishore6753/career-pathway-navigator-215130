#!/usr/bin/env python3
"""
Seed Supabase database from provided MVP documents and spreadsheets.

Features:
- Loads environment (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY).
- Parses Excel files (pandas+openpyxl) and role-card .txt files.
- Normalizes competency levels to enum: beginner|intermediate|advanced.
- Idempotent upserts with on_conflict keys.
- Optional --dry-run (no writes) and --reset (truncate tables) and --limit N (cap rows).
- Verification at end: counts per table.

Tables (assumed to exist):
- competencies (unique: name)
- roles (unique: name)
- role_competencies (unique: role_id,competency_id)  fields: role_id, competency_id, level
- role_adjacency (unique: source_role_id,target_role_id)  fields: source_role_id, target_role_id, score
- learning_resources (unique: competency_id,url)  fields: competency_id, url, title

Usage examples:
- Dry-run to show counts:
  python scripts/seed_supabase.py --dry-run

- Full seed:
  python scripts/seed_supabase.py

- With reset and limit (smoke test):
  python scripts/seed_supabase.py --reset --limit 200
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

# Ensure backend src is importable (this script lives under repo_root/scripts)
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "career_navigator_backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from seed.loaders import (  # type: ignore
    load_competency_mapping_excel,
    load_role_adjacency_excel,
    load_role_card_texts,
    load_role_navigator_excel,
)
from seed.transform import (  # type: ignore
    attach_ids_for_relationships,
    attach_ids_for_role_adjacency,
    build_from_competency_mapping,
    build_role_adjacency_records,
    build_roles_from_cards,
    build_learning_resources_from_navigator,
)
from seed.upsert import delete_all, get_count, get_id_map, get_supabase_client, upsert_in_batches  # type: ignore


DEFAULT_ATTACHMENTS = Path("/home/kavia/workspace/code-generation/attachments")

DEFAULT_COMPETENCY_XLSX = DEFAULT_ATTACHMENTS / "20251127_064753_Competency_mapping.xlsx"
DEFAULT_ROLE_ADJ_XLSX = [
    DEFAULT_ATTACHMENTS / "20251127_064751_CA_Role_Adjacency.xlsx",
    DEFAULT_ATTACHMENTS / "20251127_064752_CA_Role_Adjacency29.xlsx",
]
DEFAULT_ROLE_NAVIGATOR_XLSX = DEFAULT_ATTACHMENTS / "20251127_064756_Role_Navigator_Worksheet.xlsx"
DEFAULT_ROLE_CARD_TXTS = [
    DEFAULT_ATTACHMENTS / "20251127_064800_The Chief Architect Role(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064750_01_Chief_Technology_Officer_AI_and_Technology(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064809_Role_Card_AppDev_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064810_Role_Card_CAIO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064811_Role_Card_CCTO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064811_Role_Card_CDAO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064813_Role_Card_CDO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064814_Role_Card_CInO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064815_Role_Card_CIO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064816_Role_Card_CPTO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064818_Role_Card_CTrO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064819_Role_Card_DigProd_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064820_Role_Card_FCTO_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064821_Role_Card_Infra_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064822_Role_Card_Ops_v3(docx).txt",
    DEFAULT_ATTACHMENTS / "20251127_064823_Role_Card_PMO_v3(docx).txt",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Supabase database from attachments.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print planned counts without writing.")
    parser.add_argument("--reset", action="store_true", help="Truncate reference tables before seeding.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of relationship rows for smoke tests.")

    parser.add_argument("--competency-xlsx", type=str, default=str(DEFAULT_COMPETENCY_XLSX))
    parser.add_argument("--role-adjacency-xlsx", type=str, nargs="*", default=[str(p) for p in DEFAULT_ROLE_ADJ_XLSX])
    parser.add_argument("--role-navigator-xlsx", type=str, default=str(DEFAULT_ROLE_NAVIGATOR_XLSX))
    parser.add_argument("--role-card-paths", type=str, nargs="*", default=[str(p) for p in DEFAULT_ROLE_CARD_TXTS])
    return parser.parse_args()


def print_summary(title: str, data: Dict[str, int]) -> None:
    print(f"\n== {title} ==")
    for k, v in data.items():
        print(f"{k}: {v}")


def main() -> None:
    load_dotenv()  # Load env vars from .env if present (without requiring it)

    args = parse_args()
    limit = args.limit if args.limit and args.limit > 0 else None

    # 1) Load files
    competency_df = load_competency_mapping_excel(args.competency_xlsx)
    adjacency_df = load_role_adjacency_excel(args.role_adjacency_xlsx)
    role_cards = load_role_card_texts(args.role_card_paths)

    # Navigator sheet for potential learning resources extraction
    try:
        navigator_df = load_role_navigator_excel(args.role_navigator_xlsx)
    except Exception:
        navigator_df = None

    # 2) Transform to rows
    comp_rows, role_rows_from_mapping, role_comp_assoc = build_from_competency_mapping(competency_df, limit=limit)
    role_rows_from_cards = build_roles_from_cards(role_cards, existing_roles=[r["name"] for r in role_rows_from_mapping])
    role_rows = role_rows_from_mapping + role_rows_from_cards

    role_adj_name_rows = build_role_adjacency_records(adjacency_df, limit=limit)

    # Learning resources from navigator sheet (optional)
    lr_name_rows = build_learning_resources_from_navigator(navigator_df, limit=limit)

    planning_counts = {
        "competencies": len(comp_rows),
        "roles": len(role_rows),
        "role_competencies": len(role_comp_assoc),
        "role_adjacency": len(role_adj_name_rows),
        "learning_resources": len(lr_name_rows),
    }

    if args.dry_run:
        print_summary("Planned rows (dry-run)", planning_counts)
        return

    # 3) Connect to Supabase (service role)
    client = get_supabase_client()

    # 4) Optional reset (truncate)
    if args.reset:
        print("Resetting tables (delete all rows): roles, competencies, role_competencies, role_adjacency, learning_resources")
        # Child tables first due to FKs
        delete_all(client, "role_competencies")
        delete_all(client, "role_adjacency")
        delete_all(client, "learning_resources")
        # Parents
        delete_all(client, "roles")
        delete_all(client, "competencies")

    # 5) Upserts
    print("Upserting competencies ...")
    comps_processed = upsert_in_batches(client, "competencies", comp_rows, on_conflict="name", batch_size=500)

    print("Upserting roles ...")
    roles_processed = upsert_in_batches(client, "roles", role_rows, on_conflict="name", batch_size=500)

    # Refresh id maps after inserting parents
    comp_map = get_id_map(client, "competencies", key_field="name", id_field="id")
    role_map = get_id_map(client, "roles", key_field="name", id_field="id")

    print("Preparing role_competencies relationships ...")
    role_comp_rows = attach_ids_for_relationships(role_comp_assoc, role_map, comp_map)

    # Some schemas may not include a 'level' column on role_competencies; drop it if present.
    role_comp_rows = [{k: v for k, v in row.items() if k != "level"} for row in role_comp_rows]

    print("Upserting role_competencies ...")
    rc_processed = upsert_in_batches(
        client,
        "role_competencies",
        role_comp_rows,
        on_conflict="role_id,competency_id",
        batch_size=1000,
    )

    print("Preparing role_adjacency relationships ...")
    role_adj_rows = attach_ids_for_role_adjacency(role_adj_name_rows, role_map)

    print("Upserting role_adjacency ...")
    ra_processed = upsert_in_batches(
        client,
        "role_adjacency",
        role_adj_rows,
        on_conflict="source_role_id,target_role_id",
        batch_size=1000,
    )

    # Prepare learning resources (optional)
    print("Preparing learning_resources ...")
    lr_rows = []
    for row in lr_name_rows:
        cname = row.get("competency_name", "")
        if not cname or cname not in comp_map:
            continue
        lr_rows.append(
            {
                "competency_id": comp_map[cname],
                "url": row.get("url"),
                "title": row.get("title"),
            }
        )

    lr_processed = 0
    if lr_rows:
        print("Upserting learning_resources ...")
        try:
            lr_processed = upsert_in_batches(
                client,
                "learning_resources",
                lr_rows,
                on_conflict="competency_id,url",
                batch_size=1000,
            )
        except Exception as exc:
            # Continue without failing the entire seed if table is not yet provisioned
            print(f"Warning: learning_resources upsert skipped due to error: {exc}")

    processed_counts = {
        "competencies_processed": comps_processed,
        "roles_processed": roles_processed,
        "role_competencies_processed": rc_processed,
        "role_adjacency_processed": ra_processed,
        "learning_resources_processed": lr_processed,
    }
    print_summary("Upsert processed (not strictly equal to inserted/updated)", processed_counts)

    # 6) Verification
    verify_counts = {
        "competencies": get_count(client, "competencies") or 0,
        "roles": get_count(client, "roles") or 0,
        "role_competencies": get_count(client, "role_competencies") or 0,
        "role_adjacency": get_count(client, "role_adjacency") or 0,
        "learning_resources": get_count(client, "learning_resources") or 0,
    }
    print_summary("Verification counts (post-seed)", verify_counts)

    # Minimal success criteria check
    if verify_counts["roles"] == 0 or verify_counts["competencies"] == 0:
        print("Warning: roles or competencies table counts are zero.")
    if verify_counts["role_competencies"] == 0 or verify_counts["role_adjacency"] == 0:
        print("Warning: relationship tables show zero rows. Check input data and mappings.")


if __name__ == "__main__":
    main()
