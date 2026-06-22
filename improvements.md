# Akt Kolaudimi Improvement Roadmap

Last updated: 2026-06-22

## Objective

Generate a professional Akt Kolaudimi from any supported technical folder while keeping
every material conclusion traceable to that project's evidence. The public PDF must stay
clean and professional; provenance, uncertainty and agent diagnostics remain internal.

## Completed

### Platform and workflow

- [x] Telegram project creation, selection, upload, status and report delivery.
- [x] Immutable file versions in PostgreSQL and objects in MinIO.
- [x] Dramatiq and Redis background parsing and generation jobs.
- [x] User-managed OpenAI, Gemini and Groq credentials and model selection.
- [x] VKM 610/2022 ingestion, rules and retrieval.
- [x] LangGraph workflow for evidence processing and Akt generation.

### Document ingestion and analysis

- [x] PDF page-aware chunks.
- [x] DOCX paragraph- and table-aware chunks.
- [x] Chunk replacement, uniqueness and source coordinates.
- [x] Textless scanned PDFs marked `needs_ocr` instead of treated as parsed text.
- [x] Persistent per-file-version AI analysis runs, batches and claims.
- [x] Analysis cache keyed by immutable file version, model and schema versions.
- [x] Resume from completed document-analysis batches.
- [x] Structured provider output, token accounting and malformed-response retry.
- [x] Claim provenance linking analysis, file version and source chunk.

### Project-level professional analysis

- [x] Canonical project facts built from persisted document claims.
- [x] Stakeholder, permit, chronology, technical, quality and economic registers.
- [x] Evidence ranking by authority, confidence and corroboration.
- [x] Conflict retention instead of silently selecting every disputed value.
- [x] Exclusion of foreign-project and unverified style-reference evidence.
- [x] Dynamic input fitting based on the selected model's context budget.

### Specialist and final generation stages

- [x] Six evidence-scoped specialist review domains.
- [x] Specialist statements validated against allowed evidence IDs.
- [x] Specialist memoranda passed into section planning and final drafting.
- [x] Clean Akt Kolaudimi PDF without checklist or internal audit sections.
- [x] Basic deterministic checks for placeholders, internal terminology, canonical facts
  and conflicting alternatives.
- [x] Failed specialist calls preserve deterministic evidence packets.

### Verification baseline

- [x] Unit coverage for parsing, document analysis, dossier consolidation, specialist
  review, model budgeting and Akt generation.
- [x] Latest complete test run: 79 passed, 1 skipped.
- [x] `.env`, API keys and local human reference documents excluded from commits.

## Next Milestone: Claim-Grounded Finalization

This is the highest-priority improvement. The current verifier checks the draft globally,
but it does not prove every material sentence. A stronger model alone can therefore write
an unsupported conclusion more convincingly.

### Deliverables

- [ ] Make the writer return an internal claim ledger alongside the clean narrative.
- [ ] Record for every material claim: section, claim type, evidence IDs, confidence and
  whether it is a documented fact, professional inference or qualification.
- [ ] Resolve every evidence ID against the current job's project and file-version
  snapshot.
- [ ] Reject unknown, cross-project, superseded or non-existent evidence references.
- [ ] Require direct evidence for completion, conformity, final measurement, testing and
  suitability-for-use conclusions.
- [ ] Detect claims that imply a physical inspection when the folder only proves document
  review.
- [ ] Produce targeted correction instructions for unsupported or contradictory claims.
- [ ] Run at most one correction pass, then verify the revised draft again.
- [ ] Render only the verified revision; keep the evidence ledger internal.
- [ ] Never return an older successful PDF when the latest generation fails.

### Acceptance Criteria

- Every material factual statement in the final Akt resolves to current-project evidence.
- Professional inferences are clearly separated from documented facts internally.
- Missing evidence creates a qualification, not an invented positive conclusion.
- The public PDF remains a clean Akt Kolaudimi without citations or system terminology.
- Verification and one correction pass have deterministic termination.

## Subsequent Improvements

### OCR and richer ingestion

- [ ] Add Albanian-capable OCR for scanned PDFs and images.
- [ ] Preserve OCR page coordinates and confidence in document chunks.
- [ ] Allow OCR results to be reviewed and reprocessed independently.
- [ ] Add XLSX worksheet, row and formula extraction.
- [ ] Add controlled conversion and extraction for legacy `.doc` files.
- [ ] Add MPP task, dependency, milestone and deadline extraction.
- [ ] Backfill chunks for files parsed before chunk persistence was introduced.

### Model routing and quota efficiency

- [ ] Support model selection by stage instead of one model for the entire workflow.
- [ ] Use a cost-efficient model for document extraction and a stronger reasoning model
  for specialist synthesis, final drafting and correction.
- [ ] Estimate calls and token volume before starting a full-folder analysis.
- [ ] Enforce provider-specific RPM, TPM and daily-request limits.
- [ ] Respect provider `Retry-After` values and pause jobs instead of exhausting retries.
- [ ] Resume quota-limited jobs without repeating completed files or batches.
- [ ] Expose document-level progress and the next retry time through `/status`.

### Reproducibility

- [ ] Persist each job's exact file-version and hash snapshot.
- [ ] Persist analysis IDs, law/rule versions, provider, model and prompt/schema versions.
- [ ] Persist stage token budgets, usage, correction attempts and final output hashes.
- [ ] Ensure uploads made during a running job cannot change that job's evidence set.

### Validation against unrelated projects

- [ ] Maintain at least two unrelated technical-folder fixtures.
- [ ] Prove that names, values and evidence cannot cross project boundaries.
- [ ] Verify that unchanged files reuse analysis and updated files use only the new version.
- [ ] Evaluate factual coverage and evidence quality rather than fixed AI wording.
- [ ] Compare generated Akts against professional acceptance criteria and human examples.

### Output polish

- [ ] Improve PDF pagination, table splitting and signature-page handling.
- [ ] Normalize Albanian labels for project types and construction stages.
- [ ] Add optional organization branding without changing evidentiary content.

## Implementation Order

1. Claim-grounded finalization and one bounded correction pass.
2. OCR for scanned PDF evidence.
3. Stage-specific model routing and quota-aware resumability.
4. XLSX, legacy DOC and MPP ingestion plus chunk backfill.
5. Reproducible job snapshots and multi-project acceptance tests.
6. Final PDF typography and pagination polish.

## Completion Criteria

This roadmap is complete when:

- no supported file is silently omitted;
- every document is analysed or has an explicit machine-readable reason why it was not;
- every material Akt claim has current-project provenance;
- unsupported evidence produces qualifications rather than invented conclusions;
- transient and quota failures resume without repeating completed work;
- unrelated projects cannot contaminate each other;
- the final PDF contains only the professional Akt;
- failed generation never exposes output from an older job;
- the same evidence produces a reproducible dossier regardless of narrative wording.
