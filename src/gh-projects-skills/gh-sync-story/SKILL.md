---
name: gh-sync-story
description: 'Sync a BMAD story to a GitHub issue and update its project board status. Use after creating or updating a story file.'
---

# GitHub Sync Story

**Goal:** Sync a single BMAD story file to its corresponding GitHub issue and update the project board status.

**Your Role:** You are an automation agent that bridges BMAD story files with GitHub Projects v2. You create or update GitHub issues from story content and set the correct project board status.

## Conventions

- Bare paths (e.g. `data/gh-project-config.yaml`) resolve from the skill root's parent module directory.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Resolve the Workflow Block

Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{project-root}/_bmad/custom/{skill-name}.toml` — team overrides
3. `{project-root}/_bmad/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 2: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs under `{project-root}` — load the referenced contents as facts. All other entries are facts verbatim.

### Step 3: Load Config

Load config from `{project-root}/_bmad/bmm/config.yaml` and resolve `project_name`, `implementation_artifacts`.

### Step 4: Load GH Project Config

Load `{project-root}/_bmad-modules/gh-projects/data/gh-project-config.yaml` — this contains all project board IDs, field mappings, and status option IDs.

## Input

This skill expects one of:
- A `story_key` (e.g., `1-1-set-up-project-scaffold`) passed as argument
- A `story_file` path passed as argument
- Auto-detection from the most recently created/updated story in `{implementation_artifacts}/`

## Execution

<workflow>

<step n="1" goal="Identify story file and extract metadata">
  <action>Determine the story file path from input arguments or auto-detect</action>
  <action>Read the story file completely</action>
  <action>Extract from filename: epic_num, story_num, story_slug (e.g., `1-1-set-up-project-scaffold` → epic 1, story 1)</action>
  <action>Extract from file content: story title (from H1), status, acceptance criteria, tasks, dev notes</action>
  <action>Read sprint-status.yaml to get the current status for this story</action>
</step>

<step n="2" goal="Find or create the GitHub issue">
  <action>Load GH project config for org, repo, project_number, project_id</action>

  <action>Search for existing issue matching this story:
    `gh issue list --repo {org}/{repo} --search "Story {epic_num}.{story_num}:" --json number,title,body --limit 5`
  </action>

  <check if="matching issue found">
    <action>Store issue_number for update</action>
    <action>GOTO step 3</action>
  </check>

  <check if="no matching issue found">
    <action>Create the issue (see step 3 for body format)</action>
    <action>Store new issue_number</action>
    <action>Add issue to project board:
      `gh project item-add {project_number} --owner {org} --url https://{ghe_host}/{repo}/issues/{issue_number}`
    </action>
    <action>Store the returned item_id</action>
  </check>
</step>

<step n="3" goal="Format and update issue body">
  <critical>NEVER use hash-number patterns like #1, #2 in the issue body — they auto-link to other issues on GitHub. Use plain numbers or parenthesized format like (AC 1), (Task 2) instead.</critical>

  <action>Format the issue body from the story file content:
    - Title: `Story {epic_num}.{story_num}: {story_title}`
    - Body structure:
      ```
      ## Story
      {user story statement from file}

      ## Acceptance Criteria
      {acceptance criteria — replace any `#N` with `(AC N)` to prevent auto-linking}

      ## Tasks
      {task list as checkboxes — replace any `#N` references with plain numbers}

      ## Dev Notes
      {dev notes section, abbreviated if very long}

      ---
      *Synced from BMAD story file: `{story_file_path}`*
      ```
  </action>

  <action>Sanitize the body: scan for any `#[0-9]` patterns and replace with non-linking alternatives</action>

  <action>Write body to a temp file and update via:
    `gh issue edit {issue_number} --repo {repo} --title "Story {epic_num}.{story_num}: {story_title}" --body-file /tmp/gh-issue-body.md`
  </action>
</step>

<step n="4" goal="Update project board status">
  <action>Load status mapping from GH project config</action>
  <action>Get the story's current status from sprint-status.yaml</action>
  <action>Map sprint status to GH project status option ID</action>

  <action>Find the project item ID for this issue:
    `gh project item-list {project_number} --owner {org} --format json --limit 100`
    Search results for the matching issue number.
  </action>

  <action>Update the status field:
    `gh project item-edit --project-id {project_id} --id {item_id} --field-id {status_field_id} --single-select-option-id {status_option_id}`
  </action>

  <action>Report: "Synced Story {epic_num}.{story_num} → Issue #{issue_number}, Status: {status}"</action>
</step>

<step n="5" goal="Record sync hashes for drift detection">
  <action>Load `{project-root}/_bmad-modules/gh-projects/data/sync-state.yaml`</action>
  <action>Compute SHA-256 of the local story file (the .md file on disk):
    `sha256sum {story_file_path}` — extract the hex digest
  </action>
  <action>Fetch the issue body just written and compute its SHA-256:
    `printf '%s' "$(gh issue view {issue_number} --repo {repo} --json body --jq '.body')" | sha256sum`
    Use `printf '%s'` to strip the trailing newline before hashing — this is the canonical method for all sync-state hashes.
  </action>
  <action>Update the entry for this story_key under `stories:` in sync-state.yaml:
    - `issue_number`: the GitHub issue number
    - `local_hash`: SHA-256 of the local story file (null if no file)
    - `remote_hash`: SHA-256 of the GitHub issue body
    - `hash_version`: copy from the top-level `hash_version` in sync-state.yaml
    - `last_synced`: current ISO timestamp
  </action>
  <action>Update top-level `last_synced` timestamp</action>
  <action>Write the updated sync-state.yaml back to disk</action>
</step>

</workflow>

## Error Handling

- If `gh` CLI is not authenticated, report and halt — do not retry auth
- If the project board is not found, report the project number and halt
- If a field ID is invalid (API returns error), suggest re-running field discovery:
  `gh project field-list {project_number} --owner {org} --format json`
- Never set Priority field automatically — this is a sprint planning decision per project conventions
