from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from normalize import clean_text, normalize_edrpou


ORGANIZER_ALIASES = ("Організатор", "organizer", "organizer_name")
EDRPOU_ALIASES = ("ЄДРПОУ", "edrpou", "registration_code")
DEFAULT_CPV_PREFIXES = ("79530000", "79540000")
TENDER_PUBLIC_URL = "https://prozorro.gov.ua/tender/"
RECENT_CUTOFF = pd.Timestamp("2025-01-01", tz="UTC")

BI_COLUMNS = {
    "lot_id": ("Ідентифікатор лота",),
    "tender_title": ("Лот",),
    "procurement_method_type": ("Процедура закупівлі",),
    "date_modified": ("Дата оголошення лота",),
    "organizer_combined": ("Організатор",),
    "cpv": ("Класифікація CPV",),
}

GENERIC_REQUIRED = (
    "tender_title",
    "procuring_entity_name",
    "procuring_entity_edrpou",
    "date_modified",
    "procurement_method_type",
    "cpv",
)
GENERIC_OPTIONAL = ("tender_id", "tender_internal_id", "contact_email", "contact_phone", "contact_person")


def _find_column(df: pd.DataFrame, aliases: Iterable[str], label: str) -> str:
    for alias in aliases:
        if alias in df.columns:
            return alias
    raise ValueError(f"Missing required column for {label}: one of {', '.join(aliases)}")


def _format_date(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (int, float)) and not pd.isna(value):
        try:
            return (pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")).isoformat()
        except (ValueError, OverflowError):
            return clean_text(value)
    parsed = pd.to_datetime(value, errors="coerce")
    return pd.Timestamp(parsed).isoformat() if pd.notna(parsed) else clean_text(value)


def _date_ts(value: object) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return parsed if pd.notna(parsed) else None


def _cpv_prefix(value: object) -> str:
    return "".join(ch for ch in clean_text(value) if ch.isdigit())[:8]


def _split_organizer(value: object) -> tuple[str, str | None]:
    raw = clean_text(value)
    if "|" not in raw:
        return raw, normalize_edrpou(raw)
    name, code = raw.rsplit("|", 1)
    return clean_text(name), normalize_edrpou(code)


def _tender_id_from_lot_id(value: object) -> str:
    return re.sub(r"-L\d+$", "", clean_text(value), flags=re.IGNORECASE)


def read_organizers(input_path: str | Path, max_organizers: int | None = None) -> pd.DataFrame:
    df = pd.read_excel(input_path, dtype=object, engine="openpyxl")
    org_col = _find_column(df, ORGANIZER_ALIASES, "organizer name")
    code_col = _find_column(df, EDRPOU_ALIASES, "registration code")
    if max_organizers:
        df = df.head(max_organizers)

    normalized = df[code_col].map(normalize_edrpou)
    counts = normalized.dropna().value_counts().to_dict()
    result = pd.DataFrame(
        {
            "organizer_name_from_input": df[org_col].map(clean_text),
            "edrpou": normalized,
        }
    )
    result = result[result["edrpou"].notna()].drop_duplicates("edrpou", keep="first")
    result["input_row_count_for_edrpou"] = result["edrpou"].map(counts).fillna(0).astype(int)
    return result.reset_index(drop=True)


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=object, engine="openpyxl")
    if path.suffix.lower() == ".tsv":
        return pd.read_csv(path, dtype=object, sep="\t", encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=object, encoding="utf-8-sig")
    raise ValueError(f"Unsupported local CPV file type: {path.suffix}")


def _is_bi_export(df: pd.DataFrame) -> bool:
    return all(any(alias in df.columns for alias in aliases) for aliases in BI_COLUMNS.values())


def _normalize_bi_export(df: pd.DataFrame) -> pd.DataFrame:
    cols = {key: _find_column(df, aliases, key) for key, aliases in BI_COLUMNS.items()}
    result = pd.DataFrame()
    result["lot_id"] = df[cols["lot_id"]].map(clean_text)
    result["tender_id"] = result["lot_id"].map(_tender_id_from_lot_id)
    result["tender_internal_id"] = result["tender_id"]
    result["tender_title"] = df[cols["tender_title"]].map(clean_text)
    result["procurement_method_type"] = df[cols["procurement_method_type"]].map(clean_text)
    result["date_modified"] = df[cols["date_modified"]].map(_format_date)
    result["cpv"] = df[cols["cpv"]].map(clean_text)
    parts = df[cols["organizer_combined"]].map(_split_organizer)
    result["procuring_entity_name"] = parts.map(lambda item: item[0])
    result["procuring_entity_edrpou"] = parts.map(lambda item: item[1])
    result["contact_email"] = ""
    result["contact_phone"] = ""
    result["contact_person"] = ""
    return result


def _normalize_generic(df: pd.DataFrame, require_tender_identifier: bool) -> pd.DataFrame:
    missing = [column for column in GENERIC_REQUIRED if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required local CPV columns: {', '.join(missing)}")
    if require_tender_identifier and "tender_id" not in df.columns and "tender_internal_id" not in df.columns:
        raise ValueError("--enrich-missing-contacts requires tender_id or tender_internal_id")
    result = df.copy()
    for column in GENERIC_OPTIONAL:
        if column not in result.columns:
            result[column] = ""
    result["date_modified"] = result["date_modified"].map(_format_date)
    return result


def read_local_cpv_matches(
    cpv_file: str | Path,
    organizers: pd.DataFrame,
    cpv_prefixes: Iterable[str] = DEFAULT_CPV_PREFIXES,
    require_tender_identifier: bool = False,
) -> tuple[list[dict], dict]:
    path = Path(cpv_file)
    df = _read_table(path)
    normalized = _normalize_bi_export(df) if _is_bi_export(df) else _normalize_generic(df, require_tender_identifier)
    allowed = {_cpv_prefix(prefix) for prefix in cpv_prefixes if _cpv_prefix(prefix)}
    if allowed:
        normalized = normalized[normalized["cpv"].map(lambda value: _cpv_prefix(value) in allowed)].copy()

    organizers_by_code = dict(zip(organizers["edrpou"], organizers["organizer_name_from_input"]))
    matches: list[dict] = []
    for row in normalized.to_dict("records"):
        code = normalize_edrpou(row.get("procuring_entity_edrpou"))
        if not code or code not in organizers_by_code:
            continue
        tender_id = clean_text(row.get("tender_id"))
        matches.append(
            {
                "organizer_name_from_input": organizers_by_code[code],
                "edrpou": code,
                "prozorro_procuring_entity_name": clean_text(row.get("procuring_entity_name")),
                "contact_email": clean_text(row.get("contact_email")),
                "contact_phone": clean_text(row.get("contact_phone")),
                "contact_person": clean_text(row.get("contact_person")),
                "tender_id": tender_id,
                "tender_internal_id": clean_text(row.get("tender_internal_id")),
                "tender_title": clean_text(row.get("tender_title")),
                "procurement_method_type": clean_text(row.get("procurement_method_type")),
                "source_url": f"{TENDER_PUBLIC_URL}{tender_id}" if tender_id else "",
                "date_modified": clean_text(row.get("date_modified")),
                "cpv": clean_text(row.get("cpv")),
                "confidence": 0,
                "contact_enriched_from_api": False,
            }
        )
    return matches, {
        "local_cpv_rows_read": len(df),
        "local_cpv_rows_after_cpv_filter": len(normalized),
        "local_cpv_matches_count": len(matches),
        "local_cpv_source_format": "bi-prozorro" if _is_bi_export(df) else "generic",
    }


def _confidence(email_count: int, last_seen: str, has_phone: bool) -> tuple[int, str]:
    last = _date_ts(last_seen)
    recent = bool(last is not None and last >= RECENT_CUTOFF)
    if email_count >= 3 and recent:
        return 95, "high_recent_email"
    if email_count >= 1 and recent:
        return 85, "recent_email"
    if email_count >= 1:
        return 65, "old_email"
    if has_phone:
        return 40, "phone_only"
    return 0, "no_match"


def summarize_contacts(organizers: pd.DataFrame, matches: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.DataFrame(matches)
    rows = []
    for organizer in organizers.to_dict("records"):
        code = organizer["edrpou"]
        subset = raw[raw["edrpou"] == code] if not raw.empty else pd.DataFrame()
        emails = [clean_text(value).lower() for value in subset.get("contact_email", []) if clean_text(value)]
        phones = [clean_text(value) for value in subset.get("contact_phone", []) if clean_text(value)]
        people = [clean_text(value) for value in subset.get("contact_person", []) if clean_text(value)]
        email_counter = Counter(emails)
        primary_email, source_count = (email_counter.most_common(1)[0] if email_counter else ("", 0))
        dates = sorted([clean_text(value) for value in subset.get("date_modified", []) if clean_text(value)])
        confidence, label = _confidence(source_count, dates[-1] if dates else "", bool(phones))
        first_match = subset.iloc[0].to_dict() if not subset.empty else {}
        rows.append(
            {
                "organizer_name_from_input": organizer["organizer_name_from_input"],
                "edrpou": code,
                "prozorro_procuring_entity_name": clean_text(first_match.get("prozorro_procuring_entity_name")),
                "primary_email": primary_email,
                "contact_phone": phones[0] if phones else "",
                "contact_person": people[0] if people else "",
                "tender_id": clean_text(first_match.get("tender_id")),
                "tender_internal_id": clean_text(first_match.get("tender_internal_id")),
                "source_url": clean_text(first_match.get("source_url")),
                "date_modified": dates[-1] if dates else "",
                "confidence": confidence,
                "confidence_label": label,
                "match_count": len(subset),
                "first_seen_date_modified": dates[0] if dates else "",
                "last_seen_date_modified": dates[-1] if dates else "",
                "email_count": len(emails),
                "primary_email_source_count": source_count,
                "input_row_count_for_edrpou": organizer.get("input_row_count_for_edrpou", 1),
                "needs_manual_review": confidence < 85,
            }
        )
    return pd.DataFrame(rows), raw


def build_input_qa(input_path: str | Path, summary_df: pd.DataFrame, max_organizers: int | None = None):
    df = pd.read_excel(input_path, dtype=object, engine="openpyxl")
    org_col = _find_column(df, ORGANIZER_ALIASES, "organizer name")
    code_col = _find_column(df, EDRPOU_ALIASES, "registration code")
    if max_organizers:
        df = df.head(max_organizers)
    qa = df.copy()
    qa.insert(0, "input_row_number", qa.index + 2)
    qa["organizer_name_from_input"] = qa[org_col].map(clean_text)
    qa["normalized_edrpou"] = qa[code_col].map(normalize_edrpou)
    counts = qa["normalized_edrpou"].dropna().value_counts()
    duplicates = qa[qa["normalized_edrpou"].isin(counts[counts > 1].index)].copy()
    if not duplicates.empty:
        duplicates["input_row_count_for_edrpou"] = duplicates["normalized_edrpou"].map(counts).fillna(0).astype(int)
    summary_codes = set(summary_df["edrpou"].dropna().astype(str))
    missing = qa[qa["normalized_edrpou"].isna() | ~qa["normalized_edrpou"].map(lambda value: str(value) in summary_codes)]
    input_qa = pd.DataFrame(
        [
            {"metric": "total_input_rows", "value": len(qa)},
            {"metric": "total_non_empty_input_rows", "value": int((qa[org_col].notna() | qa[code_col].notna()).sum())},
            {"metric": "total_unique_edrpou", "value": int(qa["normalized_edrpou"].nunique(dropna=True))},
            {"metric": "duplicate_edrpou_count", "value": int((counts > 1).sum())},
            {"metric": "invalid_or_empty_edrpou_count", "value": int(qa["normalized_edrpou"].isna().sum())},
            {"metric": "summary_rows_count", "value": len(summary_df)},
            {"metric": "input_rows_missing_from_summary_count", "value": len(missing)},
        ]
    )
    return input_qa, missing, duplicates


def apply_fallback_matches(summary_df: pd.DataFrame, fallback_matches: list[dict]):
    fallback_raw = pd.DataFrame(fallback_matches)
    if fallback_raw.empty:
        return summary_df, fallback_raw, {"fallback_raw_matches_count": 0, "fallback_organizers_with_email_count": 0, "remaining_no_match_after_fallback": int(summary_df["primary_email"].fillna("").eq("").sum())}
    result = summary_df.copy()
    fallback_raw["edrpou"] = fallback_raw["edrpou"].map(normalize_edrpou)
    updated = 0
    for code, group in fallback_raw.groupby("edrpou"):
        emails = [clean_text(value).lower() for value in group["fallback_email"] if clean_text(value)]
        if not emails:
            continue
        idx = result.index[result["edrpou"] == code]
        if idx.empty or clean_text(result.loc[idx[0], "primary_email"]):
            continue
        best = group.iloc[0]
        result.loc[idx[0], "primary_email"] = Counter(emails).most_common(1)[0][0]
        result.loc[idx[0], "contact_phone"] = clean_text(best.get("fallback_phone"))
        result.loc[idx[0], "contact_person"] = clean_text(best.get("fallback_contact_person"))
        result.loc[idx[0], "confidence"] = 70
        result.loc[idx[0], "confidence_label"] = "fallback_any_tender"
        result.loc[idx[0], "needs_manual_review"] = True
        for column in ("fallback_email", "fallback_phone", "fallback_contact_person", "fallback_tender_id", "fallback_tender_title", "fallback_tender_date_modified", "fallback_source_url", "fallback_procurement_method_type"):
            result.loc[idx[0], column] = clean_text(best.get(column))
        updated += 1
    return result, fallback_raw, {
        "fallback_raw_matches_count": len(fallback_raw),
        "fallback_organizers_with_email_count": updated,
        "remaining_no_match_after_fallback": int(result["primary_email"].fillna("").eq("").sum()),
    }


def write_results(output_path: str | Path, summary_df: pd.DataFrame, raw_df: pd.DataFrame, **sheets) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary_by_organizer", index=False)
        raw_df.to_excel(writer, sheet_name="raw_prozorro_matches", index=False)
        pd.DataFrame([{"metric": k, "value": v} for k, v in sheets.get("run_summary", {}).items()]).to_excel(writer, sheet_name="run_summary", index=False)
        for key, sheet_name in (("input_qa_df", "input_qa"), ("missing_from_summary_df", "missing_from_summary"), ("duplicate_edrpou_df", "duplicate_edrpou"), ("fallback_raw_df", "fallback_raw_matches")):
            frame = sheets.get(key)
            if frame is not None:
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
