---
name: make-skill-template
description: Create or update repo-local GitHub Copilot/Codex skills under .github/skills. Use this when the user wants a new skill, wants to document a reusable workflow as a skill, or wants to improve an existing skill in this repository.
argument-hint: [skill purpose] [scope] [scripts/references]
---

# Make Skill Template

Create skills in this repo under `.github/skills/<skill-name>/`.

Read [references/links.md](/Users/aydin/Desktop/apps/ps-automation/.github/skills/make-skill-template/references/links.md) if you need the official GitHub or VS Code skill conventions while authoring.

## Workflow

1. Understand the reusable workflow first.
2. Reuse an existing repo script if one already covers the task.
3. If the workflow is not encoded yet, add the minimum script or reference needed outside the skill first.
4. Create `.github/skills/<skill-name>/SKILL.md`.
5. Add `references/` only when the SKILL body would otherwise get long or variant-heavy.
6. Keep the skill operational: commands, file paths, defaults, validation steps, and failure handling.

## Authoring rules

- Use lowercase hyphenated folder names.
- Keep `SKILL.md` concise and trigger-oriented.
- Put long examples, schemas, or command variants in `references/`.
- Prefer repo-relative canonical commands that can be run as-is.
- If the skill depends on local config, name the exact file path.
- If the skill depends on a repo script, reference the exact script path.
- Do not add extra README or changelog files inside the skill.

## Frontmatter

Always include:

- `name`
- `description`

Use optional fields only when useful:

- `argument-hint`
- `allowed-tools`
- `user-invocable`
- `disable-model-invocation`

## Quality bar

- The description must say when the skill should trigger.
- The body must tell the agent exactly which files, scripts, configs, and commands to use.
- Validation should be cheap: `--help`, dry-run, or one realistic example.
- If the workflow is fragile, document the safe default command, not just the raw script name.

## Repo conventions

- Reusable executable logic belongs in the main repo, usually under `scripts/`.
- Skills should orchestrate that logic, not duplicate it.
- When a workflow changes, update both the script docs and the skill that points to it.
