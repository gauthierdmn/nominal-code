The following JSON has syntax errors. Pay special attention to:
- Double quotes inside string values MUST be escaped as \"
- The `suggestion` fields often contain code with double-quoted strings that need escaping
- No trailing commas after the last element in arrays or objects

The expected structure is:
{{"summary": "...", "comments": [{{"path": "...", "line": N, "body": "...", "suggestion": "optional code"}}]}}

Fix this JSON and output ONLY valid JSON:

{broken_json}
