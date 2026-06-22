# Akt Kolaudimi Improvement Roadmap

## 1. Objective

Move the current system from excerpt-based Akt generation to a complete, traceable
technical-folder analysis pipeline.

The target workflow must:

- process every supported document without silent omission;
- analyse complete documents across multiple chunks when required;
- preserve source provenance for every material fact;
- consolidate document-level evidence into one project dossier;
- generate a clean professional Akt-Kolaudimi;
- verify and correct unsupported or conflicting claims before rendering;
- remain dynamic for unrelated projects and technical folders;
- reuse completed analysis when a file version has not changed.

## 2. Current Baseline

The current implementation already provides:

- Telegram project creation and project selection;
- ZIP and individual file upload;
- immutable file versions in PostgreSQL and MinIO;
- asynchronous parsing and review jobs through Dramatiq and Redis;
- PDF and DOCX text extraction;
- deterministic document classification;
- VKM 610/2022 law and rule loading;
- a LangGraph workflow for dossier construction and Akt generation;
- user-owned OpenAI, Gemini and Groq credentials;
- dynamic model token budgets;
- Gemini-generated structured Akt narrative;
- deterministic claim checks;
- JSON and PDF output storage and Telegram delivery.

The main limitation is that the final AI writer receives selected excerpts rather than
a persistent analysis of every complete document.

## 3. Stabilize the Baseline

Before changing the architecture:

1. Run the complete unit test suite.
2. Review the current uncommitted diff.
3. Commit the professional dossier, AI writer, claim verifier, clean PDF and Telegram
   report-selection changes as one checkpoint.
4. Keep `.env`, API keys and local reference documents outside version control.

Current progress:

- [x] Professional Akt baseline committed and pushed.
- [x] PDF page chunks implemented.
- [x] DOCX paragraph and table chunks implemented.
- [x] Chunk replacement and uniqueness constraints implemented.
- [ ] Existing parsed documents backfilled with chunks.
- [ ] XLSX, legacy DOC and MPP ingestion implemented.
- [x] Persistent per-document AI analysis implemented.

## 4. Complete Document Ingestion

Every imported file must end in one explicit state:

- `parsed`;
- `parsed_with_ocr`;
- `unsupported`, with a reason;
- `failed`, with a reason.

Required parser work:

- create page-aware chunks for PDF files;
- create paragraph- and table-aware chunks for DOCX files;
- extract worksheets, rows and important formulas from XLSX files;
- add a controlled conversion path for legacy `.doc` files;
- extract tasks, dates, dependencies and milestones from `.mpp` schedules;
- retain page, table, sheet and paragraph coordinates in chunk metadata;
- add OCR for scanned PDFs and images in a later iteration if still deferred.

The existing `document_chunks` table should become the source of document evidence.
Chunk boundaries must preserve enough context for technical and legal interpretation.

## 5. Persistent Per-Document Analysis

Analyse each file version independently before project-level synthesis. Long documents
must be processed through all their chunks across multiple model calls when necessary.

Persist an immutable analysis run containing:

- file version and SHA-256 hash;
- provider and model;
- prompt and schema version;
- analysis status and error details;
- token usage and timestamps;
- structured document summary;
- extracted claims and source references.

The document analysis schema should cover:

- document purpose and authoritative role;
- project identity and location;
- involved parties and professional roles;
- permits, protocols, licenses and property references;
- dates and chronology events;
- contracts, quantities and economic values;
- technical parameters and design data;
- construction phases and executed works;
- hidden works and control acts;
- materials, tests and quality certificates;
- declarations, reservations and conclusions;
- internal conflicts and uncertain values.

Every extracted claim should retain:

- normalized field name;
- original value;
- normalized value;
- source file version;
- page, paragraph, table, sheet or chunk reference;
- short supporting excerpt;
- confidence and extraction method.

Unchanged file versions should reuse a compatible completed analysis instead of making
new API requests.

## 6. Project Dossier Consolidation

Build the professional project dossier from persisted document analyses rather than
directly from truncated raw text.

The dossier reducer should produce:

- canonical project facts;
- stakeholder and professional register;
- property, permit and license register;
- project parameters;
- construction chronology;
- technical-work register;
- material and test register;
- contractual and economic summary;
- conflicts and alternative values;
- evidence coverage by professional section.

Canonical values should be selected using generic criteria:

- document authority;
- extraction confidence;
- classification confidence;
- corroboration across independent documents;
- document-to-project relationship;
- recency where legally relevant.

No project-specific names, values, filenames or expected conclusions may be embedded in
production logic.

Current progress:

- [x] Build canonical professional registers from persisted, verified claims.
- [x] Match analyses to immutable current file versions and SHA-256 hashes.
- [x] Preserve analysis, claim, file-version and chunk provenance in register entries.
- [x] Rank evidence by authority, extraction confidence and corroboration.
- [x] Calculate chronology, economic and analysis-coverage integrity results.
- [x] Exclude style references, foreign-project evidence and unverified excerpts.
- [x] Feed consolidated registers to section planning and final Akt generation.
- [x] Fit consolidated writer input dynamically to the selected model budget.

## 7. Specialist Review Stages

Create structured specialist memoranda before drafting the final Akt:

- legal and administrative documentation;
- project parameters, permits and property data;
- chronology, deadlines and completion evidence;
- structural phases and hidden works;
- materials, tests and quality evidence;
- contracts, quantities and economic data.

These stages should consume document analyses and canonical evidence. They should not
independently invent project facts or rely on one large raw-document prompt.

## 8. Akt Generation And Correction

The final writer should receive:

- the canonical project dossier;
- specialist memoranda;
- verified VKM 610 references;
- section requirements;
- material conflicts and qualifications;
- source-backed evidence summaries.

The public output must remain a clean professional Akt-Kolaudimi, not an audit report,
checklist or internal diagnostic document.

Use a bounded correction workflow:

```text
draft
-> claim verification
-> correction instructions
-> revised draft
-> final verification
-> PDF rendering
```

Allow no more than one or two correction iterations. A correction should target only
unsupported, contradictory or malformed sections.

## 9. Claim Verification

Verify every material public claim against the consolidated evidence store.

Checks should include:

- canonical value consistency;
- source existence;
- source-to-project relationship;
- legal-reference validity;
- chronology consistency;
- arithmetic consistency for economic values;
- unsupported physical-inspection statements;
- placeholders and internal system terminology;
- duplicated or contradictory conclusions.

Human-review metadata and undefined optional facts are valid outcomes. They should not
be treated automatically as generation failures.

## 10. Reproducibility And Job Snapshots

Each generation job should permanently record:

- project file-version IDs and hashes;
- document-analysis IDs;
- law-document and rule versions;
- provider and model;
- prompt and schema versions;
- token budgets and usage;
- complete agent trace;
- correction attempts;
- output hashes.

A running job must use its original input snapshot even if the user uploads a newer file
version while generation is in progress.

## 11. Rate Limits And Resumability

Per-document analysis will require multiple API calls. The worker pipeline must:

- respect provider and model request limits;
- checkpoint every completed document and chunk;
- retry transient failures with bounded backoff;
- resume without repeating successful work;
- expose meaningful progress to `/status`;
- allow failed documents to be retried independently;
- avoid logging or persisting decrypted API keys.

## 12. Testing Strategy

Add tests at four levels.

### Unit Tests

- parser and chunk boundaries;
- document-analysis schema validation;
- claim normalization and source references;
- canonical fact ranking;
- conflict detection;
- token-budget allocation;
- correction-loop termination.

### Integration Tests

- upload to parse to chunks;
- chunks to document analysis;
- cached analysis reuse;
- project dossier consolidation;
- job input snapshot isolation;
- JSON and PDF output persistence.

### Dynamic Project Tests

Use at least two unrelated technical folders and verify:

- no names, values or evidence cross project boundaries;
- every public project fact comes from that project's file versions;
- reference examples influence style only;
- regeneration after a file update uses the new version only.

### Professional Acceptance Tests

Maintain expected evidence fixtures rather than fixed AI prose. Validate:

- required factual coverage;
- chronology and economic consistency;
- source traceability;
- professional section coverage;
- absence of checklist and internal workflow language.

## 13. Implementation Order

1. Stabilize and commit the current baseline.
2. Populate `document_chunks` during parsing.
3. Add persistent document-analysis runs and claims.
4. Implement resumable per-document AI analysis.
5. Rebuild the professional dossier from persisted claims.
6. Add specialist memoranda.
7. Add the bounded writer correction loop.
8. Add full provenance and reproducibility metadata.
9. Validate against multiple unrelated technical folders.
10. Polish PDF pagination and typography after evidence quality is stable.

## 14. Completion Criteria

This improvement phase is complete when:

- no supported file is silently ignored;
- every parsed page, paragraph, table or sheet is represented by chunks;
- every document is fully analysed or explicitly reports why it was not;
- every material Akt claim has source provenance;
- unrelated projects cannot contaminate each other;
- unchanged documents reuse cached analyses;
- transient failures can resume safely;
- the final PDF contains only the professional Akt;
- a failed generation never exposes an output from an older job;
- the same evidence produces a reproducible dossier regardless of narrative wording.
