# Knowledge Evolution Sprint 1

Branch: `feature/knowledge-evolution-1`. Source-only extension of the existing
Fusion → Intelligence → RC → Product store chain. No new dependencies or services.

## Implemented

- Multi-source topic packages retain immutable source versions, citation hashes,
  claim snapshots, Evidence and external reports. Relevant claims without a chunk
  are included so that missing Evidence remains visible.
- Claim comparisons distinguish exact text, lexical overlap and potential conflicts
  suggested by negation tokens. Missing/invalid Evidence is reported separately.
  These bounded lexical rules do **not** provide semantic analysis or fact checking.
- Each imported synthesis retains package, prompt and input hashes, optional parent
  synthesis, model/origin metadata and explicit claim/Evidence references. Unknown
  references, Evidence from other claims, foreign package IDs and parents from
  another package are rejected before persistence. Existing manual imports remain
  compatible. Repeating an identical import is idempotent.
- Synthesis comparison uses exact text, kind and provenance, independently of item
  order. It reports added, removed and unchanged conclusions and separate decision
  ledgers. It does not judge model quality or factual accuracy.
- Fusion UI displays Source Claims, External Verification, lexical findings,
  claim/Evidence IDs and parent versions. Download/import use existing authenticated
  local HTTP routes. The response template includes provenance fields.
- Existing user review and practical-task workflow is reused: acceptance plus valid
  references is required; later rejection flags the historical task for review.
  Original claims and Evidence are never promoted into AI conclusions or overwritten.

## Manual workflow

1. Import synthetic courses/documents or a project. Create Source Claims in Knowledge.
2. In Fusion select sources, topic and analysis task. Review claims, gaps and full
   package; download JSON and manually supply it to ChatGPT. No API is called.
3. Import JSON using the displayed schema. For another version, set
   `parent_synthesis_id` to the earlier synthesis ID from the same package.
4. Compare versions in Intelligence. Review individual conclusions in Fusion.
5. Explicitly accept a conclusion, create its practical task and record completion.

## Validation

New unit tests cover claim comparisons/gaps, atomic reference rejection, scoped
parent versions, append-only storage, restart persistence, order-independent
comparison and acceptance/task lineage. New HTTP acceptance executes the synthetic
package → export → import → version → comparison → decision → task → restart cycle.
New JavaScript checks exercise response-template provenance and missing Evidence.
All are integrated into `python3 -B scripts/cloud_checks.py`, alongside existing
unit, HTTP and JavaScript checks. Exact execution results are recorded below after
running the checks. Runtime data stays in temporary directories/test_artifacts.

## Limits

Selection uses lexical search, up to 10 sources/40 citations/40 claims and a 256 KiB
citation context. No semantic completeness, real external verification or automated
ChatGPT submission is claimed. Model identity/time and external reports are supplied
by the user. References establish provenance, not truth. User decisions remain their
own append-only ledger. No Linux runtime probe was repeated for this feature.

## Executed results

Final `python3 -B scripts/cloud_checks.py`: PASS on Linux, 29 unit tests (7 new),
25 existing + 17 new HTTP assertions, 3 JavaScript syntax checks and 36 JavaScript
assertions (8 new). Source audit PASS; zero AI calls. HTTP tests required the
sandbox allowance for local socket creation and bound only to 127.0.0.1; test
network guards blocked external connections. No dependencies were installed.
`git diff --check`: PASS. No main merge, runtime/user-data changes or remote
publication. A local PR draft is in PULL_REQUEST_DRAFT.md.
