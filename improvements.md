# Akt Kolaudimi Improvement Roadmap

Last updated: 2026-06-25

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
- [x] Project-wide canonical facts separated from document metadata, work-stage facts and
  contract-reference facts.
- [x] Albanian aliases such as `emri_objektit`, `sipermarresi`,
  `kontrata_sipermarrjes`, `date_of_document` and `element_name` normalized before
  dossier consolidation.
- [x] Targeted role/contract extraction for kolaudator, supervisor and contractor
  contracts even when document-analysis cache is already present.
- [x] Contract reference extraction for Albanian notarial `Rep./Kol.` formats.
- [x] Exclusion of foreign-project and unverified style-reference evidence.
- [x] Dynamic input fitting based on the selected model's context budget.

### Specialist and final generation stages

- [x] Six evidence-scoped specialist review domains.
- [x] Specialist statements validated against allowed evidence IDs.
- [x] Specialist memoranda passed into section planning and final drafting.
- [x] Clean Akt Kolaudimi PDF without checklist or internal audit sections.
- [x] Basic deterministic checks for placeholders, internal terminology, canonical facts
  and conflicting alternatives.
- [x] Conflict diagnostics show the disputed field, canonical value, used alternative and
  source documents when publication is blocked.
- [x] Publish gate blocks material party/permit conflicts while treating object-name
  wording variants as diagnostic unless stronger evidence marks a foreign project.
- [x] Professional conclusion guard rejects unsigned authorization/use-approval language
  and requires both declaration-level and technical/project evidence for conformity.
- [x] Professional conclusion guard also rejects generated final-acceptance wording such
  as `punimet janë pranuar` and `struktura është funksionale`.
- [x] Professional conclusion levels are enforced for every material paragraph:
  `proven`, `qualified` or `not_proven`.
- [x] Required public technical-economic details are selected from the dossier and
  verified before publication.
- [x] Failed specialist calls preserve deterministic evidence packets.

### Verification baseline

- [x] Unit coverage for parsing, document analysis, dossier consolidation, specialist
  review, model budgeting and Akt generation.
- [x] Latest complete test run: 110 passed, 1 skipped.
- [x] `.env`, API keys and local human reference documents excluded from commits.

## Completed Milestone: Claim-Grounded Finalization

This is the highest-priority improvement. The current verifier checks the draft globally,
but it does not prove every material sentence. A stronger model alone can therefore write
an unsupported conclusion more convincingly.

### Deliverables

- [x] Make the writer return an internal claim ledger alongside the clean narrative.
- [x] Record for every material claim: section, claim type, evidence IDs, confidence and
  whether it is a documented fact, professional inference or qualification.
- [x] Resolve every evidence ID against the current job's project and file-version
  snapshot.
- [x] Reject unknown, cross-project, superseded or non-existent evidence references.
- [x] Require direct evidence for completion, conformity, final measurement, testing and
  suitability-for-use conclusions.
- [x] Detect claims that imply a physical inspection when the folder only proves document
  review.
- [x] Produce targeted correction instructions for unsupported or contradictory claims.
- [x] Include selected and alternative source documents in conflict correction
  instructions.
- [x] Run at most one correction pass, then verify the revised draft again.
- [x] Give the correction pass bounded supplemental evidence for failed conformity,
  completion, measurement, testing and suitability claims instead of limiting it to
  the bad draft's incomplete citations.
- [x] Render only the verified revision; keep the evidence ledger internal.
- [x] Never return an older successful PDF when the latest generation fails.

### Acceptance Criteria

- Every material factual statement in the final Akt resolves to current-project evidence.
- Professional inferences are clearly separated from documented facts internally.
- Missing evidence creates a qualification, not an invented positive conclusion.
- The public PDF remains a clean Akt Kolaudimi without citations or system terminology.
- Verification and one correction pass have deterministic termination.

## Completed Milestone: OCR For Scanned Evidence

### OCR deliverables

- [x] Add local Albanian/English Tesseract OCR for scanned PDFs and images.
- [x] OCR only PDF pages without an extractable text layer.
- [x] Preserve page coordinates, confidence, DPI, language and engine version in chunks.
- [x] Mark usable OCR evidence as `parsed_with_ocr` and include it in every analysis stage.
- [x] Keep page-level OCR failures explicit without discarding successful pages.
- [x] Expose versioned chunks for OCR review and allow independent reprocessing.

## Subsequent Improvements

### Richer ingestion

- [ ] Add XLSX worksheet, row and formula extraction.
- [ ] Add controlled conversion and extraction for legacy `.doc` files.
- [ ] Add MPP task, dependency, milestone and deadline extraction.
- [ ] Backfill chunks for files parsed before chunk persistence was introduced.

### Model routing and quota efficiency

- [x] Support model selection by stage instead of one model for the entire workflow.
- [x] Use a cost-efficient model for document extraction and a stronger reasoning model
  for specialist synthesis, final drafting and correction.
- [x] Estimate calls and token volume before starting a full-folder analysis.
- [x] Use conservative model-specific RPM pacing for known Gemini free-tier models and
  user-configured request limits.
- [x] Respect provider `Retry-After` values and pause jobs instead of exhausting retries.
- [x] Resume quota-limited jobs without repeating completed files or batches.
- [x] Expose document-level progress and the next retry time through `/status`.
- [ ] Add proactive TPM/RPD budget enforcement before provider calls when exact
  account limits are known.

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
