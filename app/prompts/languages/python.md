# Python Style Guidelines

## Type Annotations

- Annotate everything: function parameters, return values, class attributes, and local variables where the type is not obvious from the right-hand side.
- Use modern union syntax with `|` — write `str | None`, not `Optional[str]` or `Union[str, None]`.
- Use lowercase built-in generics — write `list[str]`, `dict[str, int]`, `tuple[str, str]`, not `List`, `Dict`, `Tuple`.
- Never use `Any` as a shortcut. Inspect the source of a third-party library to find the real type. `Any` is only acceptable at genuine serialization boundaries (e.g. `json.loads`, `response.json()`) where the data is inherently untyped.
- Guard type-only imports behind `TYPE_CHECKING` to avoid circular imports at runtime.

## Naming

- No single-letter variables. Use descriptive names (`for index in range(10)`, not `for i in range(10)`).
- `snake_case` for functions, methods, variables, and parameters.
- `PascalCase` for classes and type aliases.
- `ALL_CAPS` for module-level constants.
- Prefix private attributes, methods, and module-level helpers with `_`.

## Imports

- Use absolute imports from the package root — no relative imports.
- Group imports in order: standard library, third-party, local, then a `TYPE_CHECKING` block.
- Mark re-exports in `__init__.py` with `# noqa: F401`.

## Classes

- Use `@dataclass(frozen=True)` for immutable data containers.
- Use `dataclasses.replace()` to derive modified copies of frozen dataclasses.
- Use `Protocol` for abstract interfaces — prefer protocols over `ABC`.
- Prefix private instance attributes with `_` and expose read-only access with `@property` when needed.

## Functions

- Return early. Check error conditions and edge cases at the top, then proceed with the happy path.
- Keep nesting shallow — guard clauses over nested `if/else` chains.
- Use `*` to enforce keyword-only arguments when a function has more than two or three parameters with similar types.
- Be explicit with parameter names — avoid `**kwargs` in both signatures and call sites. Pass named arguments directly instead of building a dict and unpacking with `**`.

## Async

- Use `asyncio.create_subprocess_exec` for external commands, never `subprocess.run` in async code.
- Use `asyncio.gather()` to run independent awaitable calls concurrently.
- Cancel tasks cleanly — `task.cancel()` followed by `await task` wrapped in `except asyncio.CancelledError`.

## Error Handling

- Catch specific exception types — never use bare `except`.
- Use `logger.exception()` when you catch an exception and want the full traceback.
- Use `logger.warning()` for non-critical failures (e.g. an API call that can be retried or safely ignored).
- Include context in raised exceptions: what failed and why.

## Logging

- Initialize per-module: `logger: logging.Logger = logging.getLogger(__name__)`.
- Use lazy formatting with `%s` placeholders — write `logger.info("Pushed %s", sha)`, not f-strings.

## Formatting

- Add a trailing comma on the last element when a structure spans multiple lines.
- Add a blank line before `return`, `for`, `if`, `with`, `try`, and `raise` — unless the statement is the first line inside a block.
- Use f-strings for variable interpolation in non-logging code.
- Use raw strings for regex patterns: `rf"@{re.escape(name)}\b"`.

## Docstrings

- Use Google-style docstrings on every function, method, and class.
- Always add a blank line after the opening `"""` and before the closing `"""`.
- Include `Args`, `Returns`, and `Raises` sections with type annotations.
- Use double backticks for inline code references: ``` ``value`` ```.
- No module-level docstrings.

## Constants

- Define at module level, after imports and before classes/functions.
- Always type-annotate: `MAX_RETRIES: int = 3`.

## Testing

- Name test functions `test_<method>_<outcome>_<variant>`.
- Mirror the source tree — `config.py` maps to `test_config.py`.
- No docstrings or type annotations in test files.
