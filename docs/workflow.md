# Workflow

This project turns procurement exports into a source-backed outreach workbook.

```text
Target organization list
  -> CPV-filtered procurement export
  -> entity normalization
  -> EDRPOU / registration-code matching
  -> tender contact enrichment
  -> fallback enrichment
  -> contact quality scoring
  -> outreach-ready workbook
```

## 1. Target Organization List

The pipeline starts with a private Excel file containing target organizations and registration codes. In the Ukrainian Prozorro use case, the key fields are:

- `Організатор`
- `ЄДРПОУ`

The registration code is normalized to an 8-digit string so that Excel numeric formatting, leading zero loss, and whitespace do not break matching.

## 2. CPV-Filtered Procurement Export

For historical analysis, the recommended source is a CPV-filtered export from BI/open-data tooling. This avoids scanning millions of public feed records.

The pipeline supports:

- generic normalized procurement exports;
- BI Prozorro-style exports with Ukrainian column names;
- lot identifiers that need to be converted to tender identifiers.

## 3. Entity Matching

The buyer registration code from the procurement export is matched against the normalized registration code from the target list.

The pipeline keeps the original input organization name and the procurement-side buyer name, making it easier to audit discrepancies.

## 4. Tender Contact Enrichment

Analytics exports often omit tender-level contact data. When enabled, the pipeline requests tender details by tender ID and extracts:

- contact email;
- contact phone;
- contact person;
- buyer name;
- buyer registration code.

Tender detail responses are cached locally to avoid repeated API calls.

## 5. Fallback Enrichment

If a target organization has no contact from the service-category procurement set, the pipeline can scan recent tenders and look for contacts from any tender by the same buyer.

Fallback contacts are separated from service-category contacts because they are less specific and require manual review.

## 6. Contact Quality Scoring

Contacts are classified by source quality, frequency, and recency. This creates review layers such as:

- service-category tender email;
- fallback procurement email;
- phone only;
- no contact.

## 7. QA and Final Workbook

The technical workbook includes raw matches and QA sheets. The final workbook is cleaner and designed for outreach planning or CRM import.

It keeps source URLs and confidence labels so that every contact can be checked before use.
