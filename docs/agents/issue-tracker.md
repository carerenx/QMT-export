# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues at `carerenx/QMT-export`.
Use the `gh` CLI for issue operations and infer the repository from the current
Git remote.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Label: `gh issue edit <number> --add-label "..."`
- Close: `gh issue close <number> --comment "..."`

When a skill says to publish a PRD or issue, create a GitHub issue. When it says
to fetch a ticket, read that issue and its comments.
