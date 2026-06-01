# Public Procurement Contact Intelligence

Turn public procurement records into a structured, source-backed B2B outreach dataset.

## Overview

Public Procurement Contact Intelligence is a Python-based data pipeline for analyzing public procurement data, identifying organizations with historical purchasing activity in selected service categories, and enriching them with publicly available procurement contact points.

The project demonstrates:

- data ingestion from Excel/CSV exports;
- entity matching by registration code;
- public API enrichment;
- contact quality classification;
- QA checks;
- Excel output suitable for review, outreach planning, or CRM import.

The original implementation was built around Prozorro-style procurement data, but the architecture is intentionally useful for similar public procurement and open-data workflows.

## Problem

Public procurement platforms contain valuable signals about which organizations buy specific services, but the data is fragmented:

- buyers are listed across many tenders and lots;
- contact data may be missing from analytics exports;
- emails may be available only inside individual tender records;
- raw exports are not directly usable for sales or business development.

For business teams, this usually turns into manual spreadsheet work: searching tenders, copying contacts, checking whether a buyer is relevant, and separating strong contacts from weak fallback records.

## Solution

The tool combines a target organization list with CPV-filtered procurement exports, matches entities by registration code, enriches missing contacts through public procurement API records, and produces a clean Excel workbook with separate contact-quality layers.

The pipeline can:

- normalize organization registration codes;
- parse BI-style procurement exports;
- match buyers against a target organization list;
- enrich missing contacts from tender-level API records;
- optionally add fallback contacts from other recent tenders by the same organization;
- score each contact by source quality and recency;
- generate QA sheets that make coverage gaps visible.

## Use Cases

- B2B service providers identifying public-sector buyers;
- sales teams preparing targeted outreach lists;
- procurement intelligence research;
- market mapping by CPV/service category;
- lead enrichment for companies working with public tenders;
- identifying organizations that previously purchased translation, consulting, IT, legal, training, or other services.

## Example Scenario

A translation company can filter procurement history by CPV codes related to written and oral translation services, match the resulting tenders against a list of target organizers, and build an outreach list with source-backed contact emails.

The same approach can be adapted for other service categories by changing the CPV-filtered input export.

## Workflow

1. Load target organization list.
2. Normalize registration codes.
3. Load CPV-filtered procurement export.
4. Parse buyer name and registration code.
5. Match buyers with target organizations.
6. Enrich missing contact details from public tender records.
7. Add fallback contacts from other procurement records by the same organization.
8. Classify contacts by source quality.
9. Export a clean outreach-ready Excel workbook.

## Source Modes

### `local-cpv-file`

Recommended for historical analysis. Use a local CPV-filtered export from BI/open-data tooling, then match it against the target organization list.

This mode supports both a generic normalized file and a BI Prozorro-style export with Ukrainian column names.

### `feed-scan`

Diagnostic mode for scanning the public procurement feed. It can be useful for small recent checks or fallback enrichment, but it is not suitable for deep historical CPV research at large scale.

### `cpv-search`

Reserved for a future public API/search endpoint that supports true CPV or query filtering. The public feed API is not suitable for deep historical CPV search without scanning a huge number of tenders.

For large historical selections, use BI Prozorro, an official open-data export, or another pre-filtered CSV/XLSX source.

## Contact Quality Layers

- `translation_tender_email` / `service_category_tender_email` - high-confidence contact from a relevant service-category tender;
- `fallback_procurement_email` - contact from another tender by the same organization;
- `phone_only` - phone found, no email;
- `no_contact` - no usable contact found.

## Output Workbook

The final outreach workbook is designed for real review work, not debugging. It contains:

- `ready_translation_contacts` - contacts found in relevant service-category tenders;
- `fallback_contacts_review` - contacts found in other tenders by the same organization;
- `no_email_to_research` - organizations still requiring manual research;
- `all_contacts_clean` - one clean row per unique organization;
- `duplicates` - duplicate registration codes from the input list;
- `qa_summary` - key coverage and quality metrics.

Technical pipeline runs may also produce raw match sheets, cache files, and logs. Those files are local-only and are intentionally excluded from Git.

## Tech Stack

- Python
- pandas
- openpyxl
- requests
- tenacity
- tqdm
- public procurement API records
- Excel/CSV exports

## Results From Real-World Internal Run

In one internal run on a real procurement dataset, the pipeline processed:

- 897 input organization rows;
- 809 unique organization registration codes;
- 6,897 CPV-filtered procurement lots;
- 1,398 matched procurement records;
- 1,367 API-enriched tender contacts;
- 715 organizations with a usable primary email after fallback enrichment.

This repository does not include real organization names, real contact emails, real procurement exports, cache files, or generated outreach workbooks.

## Repository Layout

```text
src/
  main.py
  excel_io.py
  normalize.py
  prozorro.py
  final_export.py

examples/
  sample_organizations.xlsx
  sample_cpv_export.xlsx

docs/
  workflow.md
  data_model.md
  responsible_use.md

input/
  .gitkeep

output/
  .gitkeep

cache/
  .gitkeep

logs/
  .gitkeep
```

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Example Usage With Synthetic Data

Run the pipeline against the synthetic sample files:

```powershell
.\.venv\Scripts\python.exe src/main.py --source-mode local-cpv-file --input "examples/sample_organizations.xlsx" --cpv-file "examples/sample_cpv_export.xlsx" --output "output/sample_pipeline_result.xlsx"
```

Create the clean outreach workbook from a technical result workbook:

```powershell
.\.venv\Scripts\python.exe src/final_export.py --source "output/sample_pipeline_result.xlsx" --output "output/sample_final_contacts.xlsx"
```

## Usage With Your Own Data

Place private input files in `input/`. They are ignored by Git.

```powershell
.\.venv\Scripts\python.exe src/main.py --source-mode local-cpv-file --input "input/your_organizations.xlsx" --cpv-file "input/your_cpv_export.xlsx" --output "output/contacts_with_sources.xlsx" --enrich-missing-contacts
```

For fallback enrichment from recent tenders:

```powershell
.\.venv\Scripts\python.exe src/main.py --source-mode local-cpv-file --input "input/your_organizations.xlsx" --cpv-file "input/your_cpv_export.xlsx" --output "output/contacts_with_fallback.xlsx" --enrich-missing-contacts --fallback-feed-for-no-match --fallback-min-date-modified 2024-01-01 --fallback-limit-pages 3000
```

## Required Input Columns

The target organization file should contain:

- `Організатор`
- `ЄДРПОУ`

The generic CPV export should contain:

- `tender_id` or `tender_internal_id`
- `tender_title`
- `procuring_entity_name`
- `procuring_entity_edrpou`
- `contact_email`
- `contact_phone`
- `contact_person`
- `date_modified`
- `procurement_method_type`
- `cpv`

The BI Prozorro-style export can use Ukrainian column names such as `Ідентифікатор лота`, `Лот`, `Організатор`, and `Класифікація CPV`.

## Responsible Use

This tool is designed for responsible use with public procurement data. Users should comply with applicable data protection, anti-spam, public procurement, and business communication rules.

The repository must not contain real contact datasets, exported email lists, private procurement exports, generated outreach files, caches, or logs.

## What This Project Demonstrates

- turning messy public data into business-ready datasets;
- practical entity matching;
- API-based data enrichment;
- QA-first Excel generation;
- source-backed contact confidence scoring;
- automation of a previously manual business-development workflow.
