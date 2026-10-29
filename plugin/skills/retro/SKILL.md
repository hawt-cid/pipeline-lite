---
name: retro
description: Self-improvement retro. Reads the session journal and friction notes since the last retro and proposes small edits to instruction files (CLAUDE.md, memory, skills) for the user to approve. Run only when the user asks.
disable-model-invocation: true
---

# Retro

Turn recent friction into small, approved instruction fixes. The kb is local only: never push it,
never send its contents to an external service.

## Rules

- Edit nothing before the user approves. The only file you may write before approval is the proposals file in `<kb>/inbox/`.
- A proposal needs at least 2 journal occurrences (entries or friction lines) or one explicit user correction. No evidence, no proposal.
- Prefer deleting or tightening an instruction over adding one. Never add a rule that duplicates an existing one.
- At most 5 proposals. Fewer is fine.

## Steps

1. Run `kb retro gather` (add `--project <card id>` to narrow, `--since ISO` to override). Note its `gathered_at`: it becomes `--until` in step 6.
2. If it reports `entries: 0`, say "Nothing new in the journal since the last retro" and stop. Do not record anything.
3. Group the friction lines and entries by recurring theme. Read only the instruction files from the "in scope" list that a theme points at; do not read the rest.
4. Write the proposals to `<kb>/inbox/retro-<date>.md` (`<kb>` = `$KB_DIR` or `~/agent-kb`). Each proposal:
   - Number them `P1`..`P5` with a short title, e.g. `P1: drop stale test hint`.
   - **Target:** absolute path of one file
   - **Evidence:** the journal entries or friction lines cited by date + project (a user correction counts alone)
   - **Why:** one or two sentences
   - **Diff:** unified diff, minimal
5. Show the proposals (target, evidence, why, diff) and ask which to accept with AskUserQuestion (`multiSelect: true`, one option per proposal, label `P<n>: <title>`; AskUserQuestion allows at most 4 options per question, so with 5 proposals split them over two multiSelect questions). Apply only the accepted ones with Edit. Skip any whose target changed so the diff no longer applies, and say so.
6. Record the outcome. Put the proposals (with their titles) and the diffs of accepted ones in a body file; `--accepted`/`--rejected` take only the comma-separated numbers, since titles may contain commas, then:

   ```bash
   kb retro record --until <gathered_at> --accepted "P1,P3" --rejected "P2" --body-file <file>
   ```

   This writes `notes/retro-<date>.md` and commits `journal/` and that note in the kb repo (local commit only). The next `kb retro gather` starts at `--until`.
7. Report what was applied, what was rejected, and the retro note id.
