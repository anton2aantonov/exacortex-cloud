# ExaCortex — isolated Cloud development sources

This source-only repository contains no personal webinar, SQLite, media, keys or backups. Application AI APIs remain disabled. Stable RC3 and production are not changed by this checkout.

## Reproduce locally or in a prepared Linux environment

Python3.11+ / SQLite snapshot support and an existing Node runtime are required for the complete suite. Core Python checks have no third-party dependencies.

```sh
python3 -B scripts/check_runtime.py
python3 -B scripts/audit_source.py
python3 -B scripts/cloud_checks.py
# equivalent shell entry point
sh scripts/test_offline.sh
```

If Node is unavailable, `--allow-missing-node` explicitly leaves JS NOT TESTED. Nothing is installed automatically. Optional text-PDF import uses `pypdf==6.10.0`; setup commands are documented in GITHUB_SETUP_GUIDE.md, not executed in this sprint.

Fresh synthetic UI only:

```sh
python3 -B scripts/start_demo.py --directory .test-data/demo-NEW --port 8765
```

Choose a NEW directory for each demo; existing data is never overwritten. Use examples/first_lesson.md. Personal saved-XAGON buttons are unavailable by design. The full acceptance script generates synthetic18/16/2 results in temporary directories.

Review CLOUD_READY_REPORT.md, LINUX_COMPATIBILITY_REPORT.md, GITHUB_SETUP_GUIDE.md and MULTI_PROJECT_WORKFLOW.md. GIT_INCLUDE_FILES.json is the exact staging list; CLOUD_FILE_MANIFEST.json contains SHA-256. Runtime reports and test DBs are ignored.

Linux/actual Codex Cloud remains NOT TESTED on this Windows machine. No publication, remote connection, billing change or real model request has been performed. Local Git preparation does not authorize a future push.
