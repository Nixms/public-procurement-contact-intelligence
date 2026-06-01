from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from excel_io import (
    apply_fallback_matches,
    build_input_qa,
    read_local_cpv_matches,
    read_organizers,
    summarize_contacts,
    write_results,
)
from prozorro import enrich_missing_contacts_from_api, find_fallback_matches, find_matches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich organizer Excel rows with contacts from the Prozorro public API."
    )
    parser.add_argument("--input", required=True, help="Input .xlsx file path")
    parser.add_argument("--output", required=True, help="Output .xlsx file path")
    parser.add_argument(
        "--source-mode",
        choices=("feed-scan", "cpv-search", "local-cpv-file"),
        default="feed-scan",
        help="Data source strategy: bounded feed scan, API CPV search, or local BI/open-data CPV export",
    )
    parser.add_argument(
        "--cpv-file",
        default=None,
        help="CSV/XLSX/TSV file for --source-mode local-cpv-file",
    )
    parser.add_argument(
        "--cpv-codes",
        default="79530000,79540000",
        help="Comma-separated CPV prefixes for local-cpv-file safety filtering",
    )
    parser.add_argument(
        "--limit-pages",
        type=int,
        default=None,
        help="Scan only the first N Prozorro API pages for a quick test run",
    )
    parser.add_argument(
        "--max-organizers",
        type=int,
        default=None,
        help="Read only the first N input rows for a quick test run",
    )
    parser.add_argument(
        "--cache",
        default="cache/prozorro_matches.jsonl",
        help="JSONL file for intermediate raw matches",
    )
    parser.add_argument(
        "--tender-details-cache",
        default="cache/tender_details.jsonl",
        help="JSONL cache for tender details fetched by --enrich-missing-contacts",
    )
    parser.add_argument(
        "--enrich-missing-contacts",
        action="store_true",
        help="For local-cpv-file mode, fetch tender details from Prozorro API to fill missing contact fields",
    )
    parser.add_argument(
        "--fallback-feed-for-no-match",
        action="store_true",
        help="For organizers without primary email, scan fresh Prozorro feed for fallback contacts from any tender",
    )
    parser.add_argument(
        "--fallback-min-date-modified",
        default="2024-01-01",
        help="Lower dateModified bound for --fallback-feed-for-no-match",
    )
    parser.add_argument(
        "--fallback-limit-pages",
        type=int,
        default=3000,
        help="Maximum Prozorro feed pages for --fallback-feed-for-no-match",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Include tenders with dateModified on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Include tenders with dateModified on or before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Read the Prozorro feed from newest to oldest using descending=1",
    )
    parser.add_argument(
        "--min-date-modified",
        default=None,
        help="Skip older tenders and stop recent scans after this lower dateModified bound (YYYY-MM-DD)",
    )
    return parser.parse_args()


def _write_cache(cache_path: str | Path, matches: list[dict]) -> None:
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as cache:
        for match in matches:
            cache.write(json.dumps(match, ensure_ascii=False) + "\n")


def _parse_cpv_codes(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def setup_logging() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(logs_dir / "run.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    args = parse_args()
    setup_logging()

    logging.info("Reading organizers from %s", args.input)
    organizers = read_organizers(args.input, max_organizers=args.max_organizers)
    logging.info("Loaded %s unique organizers with normalized EDRPOU", len(organizers))

    logging.info("Using source mode: %s", args.source_mode)
    if args.enrich_missing_contacts and args.source_mode != "local-cpv-file":
        raise ValueError("--enrich-missing-contacts can only be used with --source-mode local-cpv-file")

    run_summary = {
        "source_mode": args.source_mode,
        "organizers_count": len(organizers),
        "api_enriched_tenders_count": 0,
        "api_enrichment_failed_count": 0,
    }

    if args.source_mode == "feed-scan":
        matches = find_matches(
            organizers.to_dict("records"),
            cache_path=args.cache,
            limit_pages=args.limit_pages,
            start_date=args.start_date,
            end_date=args.end_date,
            reverse=args.reverse,
            min_date_modified=args.min_date_modified,
        )
    elif args.source_mode == "local-cpv-file":
        if not args.cpv_file:
            raise ValueError("--cpv-file is required when --source-mode local-cpv-file")
        matches, local_stats = read_local_cpv_matches(
            args.cpv_file,
            organizers,
            cpv_prefixes=_parse_cpv_codes(args.cpv_codes),
            require_tender_identifier=args.enrich_missing_contacts,
        )
        run_summary.update(local_stats)
        if args.enrich_missing_contacts:
            matches, enrichment_stats = enrich_missing_contacts_from_api(
                matches,
                detail_cache_path=args.tender_details_cache,
            )
            run_summary.update(enrichment_stats)
        _write_cache(args.cache, matches)
        logging.info("Loaded %s matches from local CPV file %s", len(matches), args.cpv_file)
    else:
        raise RuntimeError(
            "cpv-search is not available for the Prozorro public feed API: tested parameters "
            "cpv, classification, classification.id, items.classification.id, query and search are ignored. "
            "Use --source-mode local-cpv-file with a BI Prozorro/open-data export for historical CPV search."
        )

    summary_df, raw_df = summarize_contacts(organizers, matches)
    fallback_raw_df = None
    fallback_checked_organizers_count = 0
    if args.fallback_feed_for_no_match:
        fallback_mask = (
            summary_df["primary_email"].fillna("").map(lambda value: str(value).strip() == "")
            | (summary_df["confidence"].fillna(0) == 0)
            | (summary_df["confidence_label"].fillna("") == "no_match")
        )
        fallback_target_edrpous = summary_df.loc[fallback_mask, "edrpou"].dropna().astype(str).tolist()
        fallback_checked_organizers_count = len(fallback_target_edrpous)
        fallback_matches = find_fallback_matches(
            fallback_target_edrpous,
            min_date_modified=args.fallback_min_date_modified,
            limit_pages=args.fallback_limit_pages,
        )
        summary_df, fallback_raw_df, fallback_stats = apply_fallback_matches(summary_df, fallback_matches)
        run_summary.update(fallback_stats)

    input_qa_df, missing_from_summary_df, duplicate_edrpou_df = build_input_qa(
        args.input,
        summary_df,
        max_organizers=args.max_organizers,
    )
    run_summary["fallback_checked_organizers_count"] = fallback_checked_organizers_count
    run_summary.setdefault("fallback_raw_matches_count", 0)
    run_summary.setdefault("fallback_organizers_with_email_count", 0)
    run_summary.setdefault("remaining_no_match_after_fallback", run_summary.get("organizers_no_match_count", 0))
    run_summary["raw_matches_count"] = len(raw_df)
    run_summary["summary_rows_count"] = len(summary_df)
    run_summary["organizers_with_primary_email_count"] = int(
        summary_df["primary_email"].fillna("").map(lambda value: str(value).strip()).ne("").sum()
    )
    run_summary["organizers_no_match_count"] = int((summary_df["match_count"] == 0).sum())
    write_results(
        args.output,
        summary_df,
        raw_df,
        run_summary=run_summary,
        input_qa_df=input_qa_df,
        missing_from_summary_df=missing_from_summary_df,
        duplicate_edrpou_df=duplicate_edrpou_df,
        fallback_raw_df=fallback_raw_df,
    )
    logging.info("Saved %s summary rows and %s raw matches to %s", len(summary_df), len(raw_df), args.output)


if __name__ == "__main__":
    main()
