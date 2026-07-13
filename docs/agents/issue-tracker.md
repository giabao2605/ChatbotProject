# Issue tracker: GitHub

Issues and PRDs for this repository live as GitHub Issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`.
- **Read an issue**: `gh issue view <number> --comments`, including labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments` with appropriate state and label filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- **Close an issue**: `gh issue close <number> --comment "..."`.

Infer the repository from the local Git remote; `gh` does this automatically inside this clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Create a GitHub Issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
