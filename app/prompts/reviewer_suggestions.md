## Suggestions

Use the `suggestion` field when you can provide a concrete, self-contained code fix:

- The `suggestion` value must be the **exact replacement code** for the target line(s). No placeholders, no `...`, no ellipsis.
- `body` becomes a brief explanation of **why** the change is needed.
- For single-line replacements, set `line` to the target line and omit `start_line`.
- For multi-line replacements, set `start_line` to the first line and `line` to the last line of the range **being replaced**. The `start_line..line` range defines exactly which lines in the file will be deleted and substituted with the `suggestion` content. Every line in this range is removed — if your suggestion does not include a line from this range, that line is deleted. If your suggestion adds lines not in this range, they are inserted.
- **Critical:** the `start_line..line` range must cover the **complete syntactic block** being replaced, including closing brackets, parentheses, and delimiters. If your replacement code includes closing `)`, `]`, `}`, or similar tokens, the range must extend to cover the **original** closing tokens too — otherwise the original closing tokens remain in the file and produce duplicates.
- Do **NOT** use suggestions for architectural advice, general observations, or changes that span large sections of code.
- Do **NOT** use suggestions for deleted lines (LEFT side comments).
