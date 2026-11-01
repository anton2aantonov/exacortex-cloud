# ExaCortex Cloud source-only rules

- Work only in this checkout; stable local RC releases and production data are read-only and out of scope.
- No paid/live LLM APIs, network publication, service connections or media processing. No secrets are required. Do not install dependencies automatically.
- Use temporary databases or NEW .test-data directories. Bind UI only to 127.0.0.1.
- Full checks: `python3 -B scripts/cloud_checks.py` (Python3.11+/SQLite deserialize + existing Node). Python-only fallback explicitly uses `--allow-missing-node`, with JS NOT TESTED.
- Runtime probe: `python3 -B scripts/check_runtime.py`. Source review: `python3 -B scripts/audit_source.py`; after staging add `--index`.
- Fresh synthetic UI: `python3 -B scripts/start_demo.py --directory .test-data/demo-NEW --port 8765`.
- Before staging, update GIT_INCLUDE_FILES.json and CLOUD_FILE_MANIFEST.json after reviewing actual new source files; never add runtime data or blindly stage the parent folder.
- One feature branch/worktree per task. Only the integrator merges shared changes. Record tests and limitations; never infer Linux PASS from Windows.
- Keep application AI transport disabled. Real cloud environment publishing, GitHub access and billing need separate user authorization.
