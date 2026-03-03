---
description: Fix Python docstring violations in nominal_code/. Pass the violation list as arguments.
argument-hint: [violations]
---

Fix each docstring violation below. For each entry, use Read to inspect the file at the given location, apply the rule's fix, then use Edit. Fix only what is listed — do not scan for additional violations.

$ARGUMENTS

## Fix rules

**Rule 1 — Opening `"""` on its own line**
Split `"""Content` into two lines: `"""` alone, then the content on the next line.

**Rule 2 — Blank line after closing `"""`**
Insert a blank line between the closing `"""` and the first line of code.

**Rule 3 — Typed `Args:` entries**
Add the type in parentheses: `name: desc` → `name (type): desc`.

**Rule 4 — Typed `Returns:` value**
Prefix the description with the return type: `desc` → `type: desc`.

**Rule 5 — No module-level docstrings**
Delete the string literal that appears at the top of the module, before any imports or definitions.
