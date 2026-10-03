---
name: coding-delegate
description: Delegate any coding, scripting, config-writing or debugging task to the installed coding agents (Claude Code, opencode, omp, Codex, Gemini CLI, aider) instead of writing large code inline. Use for "buat script", "perbaiki kode", "buat docker-compose", "buat bot", refactors, and fixing errors in a repo.
version: 1.0.0
author: jeaniro07
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [coding, delegation, claude-code, opencode, omp, codex, aider]
    category: software-development
    requires_toolsets: [terminal]
    related_skills: [homelab-services]
---

# Coding Delegate

The user installed dedicated coding agents on this machine. They are better at
code than you are inline. **Use them.**

```bash
D="bash $SKILL_DIR/scripts/code-delegate.sh"
$D --list                                          # which are installed
$D --dir /path/to/project "clear task"            # auto-select best installed
$D --tool claude   --dir /srv/app "task"           # force Claude Code
$D --tool opencode --dir /srv/app "task"
$D --tool omp      --dir /srv/app "task"
```

## When

- Writing or changing more than ~15 lines of code, a Dockerfile, a compose file,
  CI, or a multi-file change → delegate.
- A one-line shell command or reading a file → do it yourself with the terminal.

## How to write the task

The coding agent has no access to this chat. Give it everything in one message:

```
Goal: <what must exist / work when done>
Location: <files/dirs>; stack: <language, framework>
Context: <error message verbatim, relevant config, service URLs>
Constraints: <don't touch X, keep API stable, no new deps>
Done when: <command that must pass, e.g. "pytest -q passes", "docker compose config ok">
```

## After it returns

1. Read the exit code and the "changes" section.
2. Verify yourself: run the "done when" command.
3. Fails → delegate again ONCE with the new error appended, or switch tool
   (`--tool opencode` if claude failed). After 2 failed rounds, report to the
   user with the exact error. Don't loop.
4. Report: tool used, files changed, verification result, and the log path.

## Rules

- No confirmation needed for delegating code changes inside a project directory;
  the user asked for the work. Ask once only before deploying to production or
  deleting data.
- Long tasks: run in the background (terminal background mode) and check the log.
- Never pass secrets in the task text; refer to the `.env` variable name.
