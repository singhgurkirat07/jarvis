# Jarvis Workspace

This directory (`workspace/`) is the dedicated sandbox for Jarvis workspace file tools:

- `workspace_list_files` (Level 1 — Safe Read-Only)
- `workspace_read_file` (Level 1 — Safe Read-Only)
- `workspace_create_file` (Level 2 — Requires Explicit User Approval)
- `workspace_write_file` (Level 2 — Requires Explicit User Approval)

File operations are strictly confined to canonical paths within this directory and restricted to approved text file extensions (`.txt`, `.md`, `.json`, `.csv`, `.py`, `.js`, `.html`, `.css`, `.yaml`, `.yml`, `.toml`, `.log`).
