---
name: gh-sync-sprint
description: 'Sync all BMAD sprint statuses to GitHub Projects board. Use after sprint planning or bulk status changes.'
---

# GitHub Sync Sprint

**Goal:** Synchronize all story statuses from sprint-status.yaml to the GitHub Projects v2 board, creating any missing issues.

**Your Role:** You are an automation agent that bulk-syncs BMAD sprint tracking to GitHub Projects. You ensure every story has a corresponding issue and every status is current on the board.

## Conventions

- Bare paths (e.g. `data/gh-project-config.yaml`) resolve from the skill root's parent module directory.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 0: Preflight Check

Run: `sh {project-root}/_bmad-modules/gh-projects/preflight.sh`

Parse the output. If the final `PREFLIGHT:` line shows `status=FAIL`:
- Report each `CHECK:` line with `status=FAIL` to the user, including the `reason` field
- **Halt activation.** Do not proceed to Step 1.

If `status=OK` (even with warnings), proceed normally. If the preflight script is not found, skip this step.

### Step 1: Resolve the Workflow Block

Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{project-root}/_bmad/custom/{skill-name}.toml` — team overrides
3. `{project-root}/_bmad/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 2: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run.

### Step 3: Load Config

Load config from `{project-root}/_bmad/bmm/config.yaml` and resolve `project_name`, `implementation_artifacts`.

### Step 4: Load GH Project Config

Load `{project-root}/_bmad-modules/gh-projects/data/gh-project-config.yaml`.

## Execution

<workflow>

<step n="1" goal="Load sprint status and project board state">
  <action>Read `{implementation_artifacts}/sprint-status.yaml` completely</action>
  <action>Extract all story entries (pattern: `N-N-slug: status`) — skip epic entries and retrospectives</action>
  <action>Load GH project config for field IDs and status mappings</action>

  <action>Fetch all project items:
    `gh project item-list {project_number} --owner {org} --format json --limit 200`
  </action>
  <action>Build a lookup map: issue_title → {item_id, issue_number, current_status}</action>
</step>

<step n="2" goal="Detect content drift and identify sync actions">
  <action>Load `{project-root}/_bmad-modules/gh-projects/data/sync-state.yaml` if it exists</action>

  <action>For each story in sprint-status.yaml:</action>
  <action>1. Parse story_key into epic_num, story_num, story_slug</action>
  <action>2. Search project items for matching issue (title contains "Story {epic_num}.{story_num}:")</action>
  <action>3. If a local story file exists at `{implementation_artifacts}/{story_key}.md`:
    - Compute its SHA-256: `sha256sum {file_path}`
    - Compare against `local_hash` in sync-state.yaml for this story_key
    - If different → mark as **LOCAL_DRIFT** (story edited since last sync)
  </action>
  <action>4. If a matching issue exists on the board:
    - Fetch its body and compute SHA-256 using the canonical method (strip trailing newline):
      `printf '%s' "$(gh issue view {issue_number} --repo {repo} --json body --jq '.body')" | sha256sum`
    - Compare against `remote_hash` in sync-state.yaml for this story_key
    - If different → mark as **REMOTE_DRIFT** (issue edited on GitHub since last sync)
  </action>
  <action>5. Determine action needed:
    - **CREATE**: No matching issue exists → needs issue creation + project add
    - **UPDATE_CONTENT**: Local story changed (LOCAL_DRIFT) but remote unchanged → re-push story to issue
    - **REMOTE_EDITED**: Remote changed (REMOTE_DRIFT) but local unchanged → warn user, skip unless forced
    - **CONFLICT**: Both local and remote changed → warn user, require manual review
    - **UPDATE_STATUS**: Issue exists, content in sync, but board status differs from sprint status
    - **SKIP**: Issue exists, content in sync, status matches
  </action>
  <action>Build action list and report planned changes (including drift warnings) before executing</action>
</step>

<step n="3" goal="Execute sync actions">
  <critical>NEVER use #N patterns in issue bodies — they auto-link. Use (AC 1), (Task 2) instead.</critical>

  <action>For CREATE actions:</action>
  <action>1. Check if a story file exists at `{implementation_artifacts}/{story_key}.md`</action>
  <action>2. If story file exists: create issue with full content (invoke gh-sync-story logic)</action>
  <action>3. If no story file (status=backlog): create minimal issue with title only:
    `gh issue create --repo {repo} --title "Story {epic_num}.{story_num}: {title_from_epics}" --body "Story not yet created. Status: backlog"`
  </action>
  <action>4. Add to project: `gh project item-add {project_number} --owner {org} --url {issue_url}`</action>

  <action>For UPDATE_STATUS actions:</action>
  <action>1. Map sprint status → GH status option ID</action>
  <action>2. Update: `gh project item-edit --project-id {project_id} --id {item_id} --field-id {status_field_id} --single-select-option-id {option_id}`</action>

  <action>Never set Priority field automatically</action>
</step>

<step n="4" goal="Record sync hashes for drift detection">
  <action>For every story that was created or had content pushed in this run:</action>
  <action>Compute SHA-256 of local story file via `sha256sum` (null if no file on disk)</action>
  <action>Fetch issue body via `gh issue view` and compute its SHA-256 using `printf '%s'` to strip trailing newline</action>
  <action>Write or update the entry in `sync-state.yaml` with local_hash, remote_hash, hash_version (from top-level), issue_number, last_synced</action>
  <action>Update the top-level `last_synced` timestamp in sync-state.yaml</action>
</step>

<step n="5" goal="Report results">
  <action>Summarize sync results:</action>
  <action>- Issues created: N</action>
  <action>- Statuses updated: N</action>
  <action>- Content re-synced (local drift): N</action>
  <action>- Remote edits detected (skipped): N</action>
  <action>- Conflicts requiring manual review: N</action>
  <action>- Already in sync: N</action>
  <action>- Errors: N (with details)</action>
  <action>Report any drift warnings with story keys and issue numbers</action>
</step>

</workflow>

## Error Handling

- If `gh` CLI is not authenticated, report and halt
- If rate-limited by GH API, report how many items were synced before the limit
- If a specific issue fails, continue with remaining items and report failures at end
- Never set Priority field automatically — sprint planning decision
