# Skill Authoring Links

Use these as the authoritative references when the local skill needs exact structure or frontmatter behavior.

- GitHub Docs: `https://docs.github.com/en/copilot/how-tos/custom-instructions/extend-copilot-chat-with-skillsets`
- VS Code Docs: `https://code.visualstudio.com/docs/copilot/customization/skills`
- Example Template: `https://github.com/github/awesome-copilot/blob/main/skills/make-skill-template/SKILL.md`

## Working rules distilled for this repo

- Store repo-local skills in `.github/skills/<skill-name>/`.
- Keep `SKILL.md` concise and trigger-focused.
- Use frontmatter to describe when the skill should run.
- Use `references/` for long command variants, schemas, or troubleshooting notes.
- Keep scripts and executable logic in the main repo unless the logic is skill-only.
