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

**If this file does not exist**, run the setup workflow:
1. Read module config from `{project-root}/_bmad/gh-projects/config.yaml` to get `ghe_host`, `gh_org`, `gh_repo`, `gh_project_number`
2. Use `gh project view` and `gh project field-list` to discover project ID, field IDs, and status option IDs
3. Write the discovered config to `{project-root}/_bmad-modules/gh-projects/data/gh-project-config.yaml`

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

<step n="2" goal="Identify sync actions needed">
  <action>For each story in sprint-status.yaml:</action>
  <action>1. Parse story_key into epic_num, story_num, story_slug</action>
  <action>2. Search project items for matching issue (title contains "Story {epic_num}.{story_num}:")</action>
  <action>3. Determine action needed:
    - **CREATE**: No matching issue exists → needs issue creation + project add
    - **UPDATE_STATUS**: Issue exists but board status differs from sprint status
    - **SKIP**: Issue exists and status matches
  </action>
  <action>Build action list and report planned changes before executing</action>
</step>

<step n="3" goal="Execute sync actions">
  <critical>NEVER use hash-number patterns like #1, #2 in issue bodies — they auto-link. Use (AC 1), (Task 2) instead.</critical>

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

<step n="4" goal="Report results">
  <action>Summarize sync results:</action>
  <action>- Issues created: N</action>
  <action>- Statuses updated: N</action>
  <action>- Already in sync: N</action>
  <action>- Errors: N (with details)</action>
  <action>Report any issues that could not be synced and why</action>
</step>

</workflow>

## Error Handling

- If `gh` CLI is not authenticated, report and halt
- If rate-limited by GH API, report how many items were synced before the limit
- If a specific issue fails, continue with remaining items and report failures at end
- Never set Priority field automatically — sprint planning decision
