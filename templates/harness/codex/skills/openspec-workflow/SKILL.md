---
name: openspec-workflow
description: Drive OpenSpec propose and apply work in a Cosmo-managed repository. Use for creating, inspecting, validating, or implementing an OpenSpec change.
---

Use the repository's raw `openspec` CLI; Cosmo initializes OpenSpec with tool
generation disabled so it does not collide with Cosmo's explicit workflow.

Start with `openspec status --change <change-id>`. Obtain the exact next
artifact instructions with `openspec instructions <artifact> --change
<change-id>`. At proposal time create only the exact id supplied by Cosmo with
`openspec new change <change-id>` and validate it using `openspec validate
<change-id>`.

During implementation, follow the existing `tasks.md`. Its checkboxes must use
literal `- [ ] N.M Description` / `- [x] N.M Description` lines because Cosmo
parses that shape. Mark an item complete only after its work is actually done.

This skill governs OpenSpec artifacts, not implementation correctness. The
repository policy in `.agent/codex/CODEX.md` and the external validation gate
remain authoritative.
