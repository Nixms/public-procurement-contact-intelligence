# Data Model

The final contact workbook is built around one row per unique organization registration code.

## Core Fields

| Field | Description |
| --- | --- |
| `organizer_name` | Organization name from the target input list. |
| `registration_code` | Normalized registration code used for matching. |
| `primary_email` | Best available email selected by the pipeline. |
| `primary_phone` | Best available phone number, when present. |
| `contact_person` | Contact person from the tender record, when available. |
| `contact_quality` | Human-readable source quality layer. |
| `confidence` | Numeric quality score. |
| `confidence_label` | Machine-readable confidence category. |
| `source_url` | URL to the source tender or procurement record. |
| `tender_id` | Public tender identifier. |
| `tender_title` | Tender or lot title used as context. |
| `match_count` | Count of matched procurement records for the organization. |
| `needs_manual_review` | Whether the row should be checked before outreach. |

## Contact Quality

`contact_quality` separates contacts by provenance:

- `translation_tender_email` or `service_category_tender_email` means the email came from a tender in the target service category.
- `fallback_procurement_email` means the email came from another procurement by the same organization.
- `phone_only` means no email was found but a phone number is available.
- `no_contact` means no usable contact was found.

## Source Tracking

Source fields are intentionally preserved. They allow analysts to verify why a contact was selected and whether it came from a relevant service-category tender or from fallback enrichment.

## QA Fields

QA sheets track:

- total input rows;
- unique registration codes;
- duplicate registration codes;
- invalid or empty registration codes;
- input rows missing from the summary;
- raw match counts;
- API enrichment counts.
