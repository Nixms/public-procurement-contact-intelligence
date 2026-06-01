from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from normalize import clean_text, normalize_edrpou


SOURCE_PATH = "output/contacts_with_fallback.xlsx"
OUTPUT_PATH = "output/final_contacts_for_outreach.xlsx"
TRANSLATION_LABELS = {"high_recent_email", "recent_email", "old_email"}
QUALITY_ORDER = {
    "translation_tender_email": 1,
    "fallback_procurement_email": 2,
    "phone_only": 3,
    "no_contact": 4,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a clean outreach workbook from enriched contacts.")
    parser.add_argument("--source", default=SOURCE_PATH)
    parser.add_argument("--output", default=OUTPUT_PATH)
    return parser.parse_args()


def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet, dtype=object, engine="openpyxl")
    except ValueError as exc:
        if f"Worksheet named '{sheet}' not found" in str(exc):
            return pd.DataFrame()
        raise


def _truthy(value: object) -> bool:
    return bool(clean_text(value))


def _prepare_summary(summary: pd.DataFrame) -> pd.DataFrame:
    df = summary.copy()
    df["edrpou"] = df["edrpou"].map(normalize_edrpou)
    for column in (
        "primary_email",
        "contact_phone",
        "contact_person",
        "confidence_label",
        "source_url",
        "date_modified",
        "fallback_email",
        "fallback_phone",
        "fallback_contact_person",
        "fallback_tender_title",
        "fallback_tender_date_modified",
        "fallback_source_url",
        "fallback_procurement_method_type",
    ):
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].map(clean_text)
    for column in ("confidence", "match_count", "input_row_count_for_edrpou"):
        if column not in df.columns:
            df[column] = 0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    return df


def _add_best_tender(summary: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    df = summary.copy()
    df["best_tender_title"] = ""
    df["best_tender_date_modified"] = df["date_modified"]
    df["best_source_url"] = df["source_url"]
    if raw.empty:
        return df
    raw = raw.copy()
    raw["edrpou"] = raw["edrpou"].map(normalize_edrpou)
    for column in ("tender_title", "date_modified", "source_url"):
        if column not in raw.columns:
            raw[column] = ""
        raw[column] = raw[column].map(clean_text)
    raw = raw.sort_values(["edrpou", "date_modified"], ascending=[True, False])
    best = raw.drop_duplicates("edrpou", keep="first").set_index("edrpou")
    for idx, row in df.iterrows():
        code = row["edrpou"]
        if code in best.index:
            df.at[idx, "best_tender_title"] = best.at[code, "tender_title"]
            df.at[idx, "best_tender_date_modified"] = best.at[code, "date_modified"]
            df.at[idx, "best_source_url"] = best.at[code, "source_url"]
    return df


def build_ready(summary: pd.DataFrame) -> pd.DataFrame:
    mask = summary["confidence_label"].isin(TRANSLATION_LABELS) | ((summary["match_count"] > 0) & summary["primary_email"].map(_truthy) & (summary["confidence_label"] != "fallback_any_tender"))
    df = summary[mask].copy()
    df["contact_quality"] = "translation_tender_email"
    df["translation_match_count"] = df["match_count"]
    df["primary_phone"] = df["contact_phone"]
    df["needs_manual_review"] = df["confidence"] < 85
    df["comment"] = ""
    df = df.rename(columns={"organizer_name_from_input": "Організатор", "edrpou": "ЄДРПОУ"})
    columns = ["Організатор", "ЄДРПОУ", "primary_email", "primary_phone", "contact_person", "contact_quality", "confidence", "confidence_label", "best_tender_title", "best_tender_date_modified", "best_source_url", "translation_match_count", "input_row_count_for_edrpou", "needs_manual_review", "comment"]
    return df.reindex(columns=columns).sort_values(["confidence", "Організатор"], ascending=[False, True])


def build_fallback(summary: pd.DataFrame) -> pd.DataFrame:
    mask = (summary["confidence_label"] == "fallback_any_tender") | summary["fallback_email"].map(_truthy)
    df = summary[mask].copy()
    df["contact_quality"] = "fallback_procurement_email"
    df["primary_phone"] = df["contact_phone"]
    df["needs_manual_review"] = True
    df["comment"] = "Email found in another procurement by the same organizer, not necessarily translation-related."
    df = df.rename(columns={"organizer_name_from_input": "Організатор", "edrpou": "ЄДРПОУ"})
    columns = ["Організатор", "ЄДРПОУ", "primary_email", "fallback_email", "fallback_phone", "fallback_contact_person", "contact_quality", "confidence", "confidence_label", "fallback_tender_title", "fallback_tender_date_modified", "fallback_source_url", "fallback_procurement_method_type", "input_row_count_for_edrpou", "needs_manual_review", "comment"]
    return df.reindex(columns=columns).sort_values("Організатор")


def build_no_email(summary: pd.DataFrame) -> pd.DataFrame:
    df = summary[~summary["primary_email"].map(_truthy)].copy()
    df["primary_phone"] = df["contact_phone"]
    df["contact_quality"] = df["primary_phone"].map(lambda value: "phone_only" if _truthy(value) else "no_contact")
    df["suggested_search_query_1"] = df["edrpou"].map(lambda code: f"{code} email")
    df["suggested_search_query_2"] = df["organizer_name_from_input"].map(lambda name: f"{name} контакти")
    df["suggested_search_query_3"] = df.apply(lambda row: f"{row['organizer_name_from_input']} ЄДРПОУ {row['edrpou']}", axis=1)
    df["comment"] = "Manual research required."
    df = df.rename(columns={"organizer_name_from_input": "Організатор", "edrpou": "ЄДРПОУ"})
    columns = ["Організатор", "ЄДРПОУ", "primary_phone", "contact_person", "contact_quality", "confidence", "confidence_label", "input_row_count_for_edrpou", "suggested_search_query_1", "suggested_search_query_2", "suggested_search_query_3", "comment"]
    return df.reindex(columns=columns).sort_values("Організатор")


def build_all(summary: pd.DataFrame, fallback_raw: pd.DataFrame) -> pd.DataFrame:
    df = summary.copy()
    fallback_counts = fallback_raw.groupby("edrpou").size().to_dict() if not fallback_raw.empty and "edrpou" in fallback_raw else {}
    df["primary_phone"] = df["contact_phone"]
    df["translation_match_count"] = df["match_count"]
    df["fallback_match_count"] = df["edrpou"].map(fallback_counts).fillna(0).astype(int)
    df["source_type"] = df.apply(lambda row: "translation_tender" if _truthy(row["primary_email"]) and row["confidence_label"] != "fallback_any_tender" else "fallback_any_tender" if _truthy(row["primary_email"]) else "phone_only" if _truthy(row["primary_phone"]) else "no_contact", axis=1)
    df["contact_quality"] = df["source_type"].map({"translation_tender": "translation_tender_email", "fallback_any_tender": "fallback_procurement_email", "phone_only": "phone_only", "no_contact": "no_contact"})
    df["source_url"] = df.apply(lambda row: row.get("fallback_source_url") if row["source_type"] == "fallback_any_tender" else row.get("best_source_url") or row.get("source_url"), axis=1)
    df["needs_manual_review"] = df.apply(lambda row: True if row["source_type"] != "translation_tender" else int(row["confidence"]) < 85, axis=1)
    df["comment"] = df["source_type"].map({"translation_tender": "", "fallback_any_tender": "Email found in another procurement by the same organizer, not necessarily translation-related.", "phone_only": "Phone found, no email.", "no_contact": "Manual research required."})
    df["quality_order"] = df["contact_quality"].map(QUALITY_ORDER).fillna(99)
    df = df.rename(columns={"organizer_name_from_input": "Організатор", "edrpou": "ЄДРПОУ"})
    columns = ["Організатор", "ЄДРПОУ", "primary_email", "primary_phone", "contact_person", "contact_quality", "confidence", "confidence_label", "source_type", "source_url", "best_tender_title", "best_tender_date_modified", "translation_match_count", "fallback_match_count", "input_row_count_for_edrpou", "needs_manual_review", "comment"]
    return df.sort_values(["quality_order", "Організатор"]).reindex(columns=columns)


def build_qa(all_contacts: pd.DataFrame, ready: pd.DataFrame, fallback: pd.DataFrame, no_email: pd.DataFrame, input_qa: pd.DataFrame, run_summary: pd.DataFrame, raw: pd.DataFrame, fallback_raw: pd.DataFrame) -> pd.DataFrame:
    def metric(frame: pd.DataFrame, name: str, default: int = 0) -> int:
        if frame.empty or "metric" not in frame or "value" not in frame:
            return default
        rows = frame.loc[frame["metric"] == name, "value"]
        return int(float(rows.iloc[0])) if not rows.empty else default

    with_email = int(all_contacts["primary_email"].map(_truthy).sum())
    total = len(all_contacts)
    values = {
        "total_unique_organizers": total,
        "total_input_rows": metric(input_qa, "total_input_rows"),
        "duplicate_edrpou_count": metric(input_qa, "duplicate_edrpou_count"),
        "invalid_or_empty_edrpou_count": metric(input_qa, "invalid_or_empty_edrpou_count"),
        "ready_translation_contacts_count": len(ready),
        "fallback_contacts_count": len(fallback),
        "no_email_count": len(no_email),
        "total_with_primary_email": with_email,
        "total_without_primary_email": total - with_email,
        "share_with_email_percent": round((with_email / total) * 100, 2) if total else 0,
        "raw_translation_matches_count": len(raw),
        "fallback_raw_matches_count": len(fallback_raw),
        "api_enriched_tenders_count": metric(run_summary, "api_enriched_tenders_count"),
        "api_enrichment_failed_count": metric(run_summary, "api_enrichment_failed_count"),
    }
    return pd.DataFrame([{"metric": key, "value": value} for key, value in values.items()])


def build_duplicates(duplicate_source: pd.DataFrame) -> pd.DataFrame:
    if duplicate_source.empty or "normalized_edrpou" not in duplicate_source:
        return pd.DataFrame(columns=["ЄДРПОУ", "input_row_count", "organizer_names"])
    name_col = "organizer_name_from_input" if "organizer_name_from_input" in duplicate_source else duplicate_source.columns[0]
    rows = []
    for code, group in duplicate_source.groupby("normalized_edrpou"):
        rows.append({"ЄДРПОУ": normalize_edrpou(code), "input_row_count": len(group), "organizer_names": "; ".join(sorted({clean_text(v) for v in group[name_col] if clean_text(v)}))})
    return pd.DataFrame(rows).sort_values("ЄДРПОУ")


def _format_workbook(path: Path) -> None:
    wb = load_workbook(path)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for column_cells in ws.columns:
            header = str(column_cells[0].value or "")
            max_len = max(len(header), *(len(str(cell.value)) for cell in column_cells[1:200] if cell.value is not None), 12)
            ws.column_dimensions[column_cells[0].column_letter].width = min(max_len + 2, 60)
            if header in {"ЄДРПОУ", "primary_email", "fallback_email"}:
                for cell in column_cells:
                    cell.number_format = "@"
    wb.save(path)


def main() -> None:
    args = parse_args()
    source = Path(args.source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = _add_best_tender(_prepare_summary(_read_sheet(source, "summary_by_organizer")), _read_sheet(source, "raw_prozorro_matches"))
    raw = _read_sheet(source, "raw_prozorro_matches")
    fallback_raw = _read_sheet(source, "fallback_raw_matches")
    input_qa = _read_sheet(source, "input_qa")
    run_summary = _read_sheet(source, "run_summary")
    duplicate_source = _read_sheet(source, "duplicate_edrpou")
    ready = build_ready(summary)
    fallback = build_fallback(summary)
    no_email = build_no_email(summary)
    all_contacts = build_all(summary, fallback_raw)
    qa = build_qa(all_contacts, ready, fallback, no_email, input_qa, run_summary, raw, fallback_raw)
    duplicates = build_duplicates(duplicate_source)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        ready.to_excel(writer, sheet_name="ready_translation_contacts", index=False)
        fallback.to_excel(writer, sheet_name="fallback_contacts_review", index=False)
        no_email.to_excel(writer, sheet_name="no_email_to_research", index=False)
        all_contacts.to_excel(writer, sheet_name="all_contacts_clean", index=False)
        qa.to_excel(writer, sheet_name="qa_summary", index=False)
        duplicates.to_excel(writer, sheet_name="duplicates", index=False)
    _format_workbook(output)
    print(f"output={output}")


if __name__ == "__main__":
    main()
