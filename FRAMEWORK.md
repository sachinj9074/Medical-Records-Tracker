# Framework notes

This project follows a schema-first, eval-driven build: define the output schema,
build the eval set before the pipeline, keep AI where judgment is genuinely
needed and code everywhere consistency matters, then ship on synthetic data with
a cost-safe demo.

## Deliberate deviations from the default framework

1. **Safety boundary as a first-class part of the spec.** The tool organises and
   explains and points to a doctor. It never diagnoses and never recommends or
   changes treatment. This is tested directly in the eval (correct-refusal and
   explanation-fidelity metrics), not left to good intentions.

2. **Firewall reshaped to local-only.** Genericization does not apply (no employer
   data). Instead, the public demo runs on synthetic records only, and real
   records stay local and are never committed or uploaded.

## Decisions added during design

- **Narrowed wedge:** the target is messy, handwritten, never-digitized paper, not
  general record storage (which portals already do).
- **Originals as source of truth:** the extracted data is a fallible convenience
  layer; the original image is authoritative and always shown.
- **Export-to-doctor** is a first-class v1 feature.
- **Printed lab flags only:** the tool never computes a clinical flag.
- **Chat retrieval deferred to v2**, behind the guard, so the safe core is proven
  first.
- **Ingestion friction (time-to-file)** is a tracked metric, because the archive
  only pays off if the upload habit sticks.

See [PROJECT_SPEC.md](PROJECT_SPEC.md) for the full spec.
