# Current local preparation

Local Git is now initialized on main with an explicit source-only staged index, no commits or remotes. Use GITHUB_SETUP_GUIDE.md for current steps. External publication still requires separate user authorization.

# Future private repository — manual approval required

1. Review every path in CLOUD_FILE_MANIFEST.json and scan for private material/credentials. The manifest is an inventory, not a guarantee against all possible secrets.
2. Only after approval, initialize Git INSIDE this source-only folder. Review git status and explicitly stage the allowlisted code/examples/docs; never stage the parent XAGON folder.
3. Review git diff --cached --name-only, git diff --cached --stat and the staged contents before committing. Do not add local libraries, transcripts, logs, recordings or backup folders.
4. Create a PRIVATE GitHub repository and authorize a limited repository connection only after separate explicit user approval. No remote, commit, push, upload or connection setting was performed in this sprint.
5. Later run the portable checks in the selected Linux/cloud environment before calling the copy Cloud-tested. No application API secrets are needed; keep live AI off.

Official environment documentation consulted: https://learn.chatgpt.com/docs/environments/cloud-environment (legacy environment guide; it links to the current experience). Account UI, access and costs must be checked separately. This folder does not provision cloud resources.
