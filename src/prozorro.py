from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm import tqdm

from normalize import clean_text, normalize_edrpou


BASE_URL = "https://public-api.prozorro.gov.ua/api/2.5/tenders"
TENDER_PUBLIC_URL = "https://prozorro.gov.ua/tender/"
SEARCH_URL = "https://prozorro.gov.ua/api/search/tenders"


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _get_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _post_json(url: str, payload: dict) -> dict:
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _contact_from_tender(tender: dict) -> dict:
    entity = tender.get("procuringEntity") or {}
    contact = entity.get("contactPoint") or {}
    identifier = entity.get("identifier") or {}
    tender_id = clean_text(tender.get("id") or tender.get("tenderID"))
    public_id = clean_text(tender.get("tenderID") or tender_id)
    return {
        "edrpou": normalize_edrpou(identifier.get("id")),
        "prozorro_procuring_entity_name": clean_text(entity.get("name")),
        "contact_email": clean_text(contact.get("email")),
        "contact_phone": clean_text(contact.get("telephone")),
        "contact_person": clean_text(contact.get("name")),
        "tender_id": public_id,
        "tender_internal_id": tender_id,
        "tender_title": clean_text(tender.get("title")),
        "procurement_method_type": clean_text(tender.get("procurementMethodType")),
        "source_url": f"{TENDER_PUBLIC_URL}{public_id}" if public_id else "",
        "date_modified": clean_text(tender.get("dateModified")),
    }


def _read_jsonl(path: str | Path) -> dict[str, dict]:
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    records: dict[str, dict] = {}
    with cache_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            key = clean_text(record.get("tender_id") or record.get("id"))
            if key:
                records[key] = record
    return records


def _append_jsonl(path: str | Path, record: dict) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def fetch_tender_details(tender_id: str) -> dict | None:
    tender_id = clean_text(tender_id)
    if not tender_id:
        return None
    try:
        search_result = _post_json(SEARCH_URL, {"text": tender_id})
        for item in search_result.get("data", []):
            candidate_id = clean_text(item.get("tenderID") or item.get("id"))
            if candidate_id == tender_id or tender_id in candidate_id:
                internal_id = clean_text(item.get("id"))
                if internal_id:
                    return _get_json(f"{BASE_URL}/{internal_id}").get("data")
    except Exception as exc:  # noqa: BLE001
        logging.debug("Search lookup failed for %s: %s", tender_id, exc)
    try:
        return _get_json(f"{BASE_URL}/{tender_id}").get("data")
    except Exception as exc:  # noqa: BLE001
        logging.warning("Tender details lookup failed for %s: %s", tender_id, exc)
        return None


def enrich_missing_contacts_from_api(matches: list[dict], detail_cache_path: str | Path) -> tuple[list[dict], dict]:
    cache = _read_jsonl(detail_cache_path)
    enriched = 0
    failed = 0
    result: list[dict] = []
    for match in tqdm(matches, desc="Enriching tender contacts"):
        row = dict(match)
        missing_contact = not any(clean_text(row.get(field)) for field in ("contact_email", "contact_phone", "contact_person"))
        tender_id = clean_text(row.get("tender_id") or row.get("tender_internal_id"))
        if missing_contact and tender_id:
            details = cache.get(tender_id)
            if details is None:
                details = fetch_tender_details(tender_id)
                _append_jsonl(detail_cache_path, {"tender_id": tender_id, "data": details})
            elif "data" in details:
                details = details.get("data")
            if details:
                contact = _contact_from_tender(details)
                for source, target in (("contact_email", "contact_email"), ("contact_phone", "contact_phone"), ("contact_person", "contact_person"), ("prozorro_procuring_entity_name", "prozorro_procuring_entity_name")):
                    if contact.get(source):
                        row[target] = contact[source]
                row["contact_enriched_from_api"] = True
                enriched += 1
            else:
                row["contact_enriched_from_api"] = False
                failed += 1
        result.append(row)
    return result, {"api_enriched_tenders_count": enriched, "api_enrichment_failed_count": failed}


def find_matches(
    organizers: list[dict],
    cache_path: str | Path,
    limit_pages: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    reverse: bool = False,
    min_date_modified: str | None = None,
) -> list[dict]:
    wanted = {row["edrpou"]: row.get("organizer_name_from_input", "") for row in organizers if row.get("edrpou")}
    url = BASE_URL
    params = {"descending": 1} if reverse else None
    matches: list[dict] = []
    page = 0
    while url:
        page += 1
        payload = _get_json(url, params=params)
        params = None
        tenders = payload.get("data", [])
        page_matches = 0
        dates = [clean_text(item.get("dateModified")) for item in tenders if clean_text(item.get("dateModified"))]
        for tender in tenders:
            contact = _contact_from_tender(tender)
            code = contact.get("edrpou")
            if code in wanted:
                contact["organizer_name_from_input"] = wanted[code]
                matches.append(contact)
                page_matches += 1
        logging.info(
            "page=%s count=%s min_dateModified=%s max_dateModified=%s matches=%s",
            page,
            len(tenders),
            min(dates) if dates else "",
            max(dates) if dates else "",
            page_matches,
        )
        if limit_pages and page >= limit_pages:
            break
        url = (payload.get("next_page") or {}).get("uri")
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(cache_path).open("w", encoding="utf-8") as handle:
        for match in matches:
            handle.write(json.dumps(match, ensure_ascii=False) + "\n")
    return matches


def find_fallback_matches(target_edrpous: Iterable[str], min_date_modified: str = "2024-01-01", limit_pages: int = 3000) -> list[dict]:
    wanted = {normalize_edrpou(code) for code in target_edrpous if normalize_edrpou(code)}
    url = BASE_URL
    params = {"descending": 1}
    matches: list[dict] = []
    for page in tqdm(range(1, limit_pages + 1), desc="Scanning fallback feed"):
        payload = _get_json(url, params=params)
        params = None
        for tender in payload.get("data", []):
            contact = _contact_from_tender(tender)
            if contact.get("edrpou") in wanted:
                matches.append(
                    {
                        "edrpou": contact["edrpou"],
                        "fallback_email": contact["contact_email"],
                        "fallback_phone": contact["contact_phone"],
                        "fallback_contact_person": contact["contact_person"],
                        "fallback_tender_id": contact["tender_id"],
                        "fallback_tender_title": contact["tender_title"],
                        "fallback_tender_date_modified": contact["date_modified"],
                        "fallback_source_url": contact["source_url"],
                        "fallback_procurement_method_type": contact["procurement_method_type"],
                    }
                )
        url = (payload.get("next_page") or {}).get("uri")
        if not url:
            break
    return matches
