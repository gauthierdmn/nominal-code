"""
Find every function/method in nominal_code/ that lacks a dedicated test.

Detection strategy (a function is "tested" if ANY of the following match):
  1. Name convention  – a test function exists whose name contains the source
                        function's bare name (underscores stripped from the
                        front), e.g. ``test_run_agent_*`` covers ``run_agent``
                        and ``_run_git``.
  2. Test-class name  – a test class exists whose name, lowercased with
                        underscores, contains the source function's bare name
                        (e.g. ``TestEnsureReady`` covers ``ensure_ready``).
  3. Direct call      – the function's bare name appears as a called
                        ``Name`` or ``Attribute`` node inside any test body.
  4. Patch reference  – the function's bare name appears as a string literal
                        inside a ``patch`` / ``patch.object`` call in tests.
  5. Import reference – the function is explicitly imported in a test module.
"""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_ROOT = Path(__file__).parent / "nominal_code"
TEST_ROOT = Path(__file__).parent / "tests"

_SKIP_DUNDER = {
    "__init_subclass__",
    "__class_getitem__",
    "__subclasshook__",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FunctionInfo:
    """
    Metadata for a single source function or method.

    Attributes:
        name (str): The bare function/method name as written in source.
        bare_name (str): ``name`` with leading underscores stripped.
        qualified_name (str): ``ClassName.method`` or just ``name``.
        file (Path): Absolute path to the source file.
        line (int): Line number of the ``def`` statement.
        class_name (str): Enclosing class name, empty for module-level funcs.
    """

    name: str
    bare_name: str
    qualified_name: str
    file: Path
    line: int
    class_name: str = ""


@dataclass
class TestIndex:
    """
    Pre-built index of everything referenced in the test suite.

    Attributes:
        function_names (set[str]): All test-function bare names, lower-cased
            and with leading underscores stripped, split by ``_`` tokens.
        class_names (set[str]): All test-class names lower-cased.
        called_names (set[str]): Every ``Name`` / ``Attribute`` node that
            appears in a call position inside test function bodies.
        patched_strings (set[str]): String literals passed to ``patch``
            calls (last dotted component extracted).
        imported_names (set[str]): Names explicitly imported from
            ``nominal_code.*`` in test modules.
    """

    function_names: set[str] = field(default_factory=set)
    class_names: set[str] = field(default_factory=set)
    called_names: set[str] = field(default_factory=set)
    patched_strings: set[str] = field(default_factory=set)
    imported_names: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


def _collect_source_files(root: Path) -> list[Path]:
    """
    Return all non-dunder Python source files under ``root``.

    Args:
        root (Path): Root directory to scan recursively.

    Returns:
        list[Path]: Sorted list of ``.py`` file paths.
    """

    return sorted(path for path in root.rglob("*.py") if path.name != "__init__.py")


def _class_for_node(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    tree: ast.Module,
) -> str:
    """
    Return the name of the immediately enclosing class, or empty string.

    Args:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The function node.
        tree (ast.Module): The parsed module tree.

    Returns:
        str: Enclosing class name, or ``""`` for module-level functions.
    """

    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.ClassDef):
            continue

        for child in ast.walk(candidate):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == node.name
                and child.lineno == node.lineno
            ):
                return candidate.name

    return ""


def extract_source_functions(root: Path) -> list[FunctionInfo]:
    """
    Walk all source files and return a ``FunctionInfo`` for every def.

    Skips ``__init__.py`` files and specific dunder helpers that are never
    tested directly (``__init_subclass__``, etc.).

    Args:
        root (Path): Root of the ``nominal_code`` package.

    Returns:
        list[FunctionInfo]: All discovered functions ordered by file/line.
    """

    results: list[FunctionInfo] = []

    for source_file in _collect_source_files(root):
        source_text: str = source_file.read_text(encoding="utf-8")
        tree: ast.Module = ast.parse(source_text, filename=str(source_file))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            if node.name in _SKIP_DUNDER:
                continue

            class_name: str = _class_for_node(node, tree)
            qualified_name: str = (
                f"{class_name}.{node.name}" if class_name else node.name
            )
            bare_name: str = node.name.strip("_")

            results.append(
                FunctionInfo(
                    name=node.name,
                    bare_name=bare_name,
                    qualified_name=qualified_name,
                    file=source_file,
                    line=node.lineno,
                    class_name=class_name,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Test index construction
# ---------------------------------------------------------------------------


def _is_patch_call(node: ast.Call) -> bool:
    """
    Return True if ``node`` is a call to ``patch`` or ``patch.object``.

    Args:
        node (ast.Call): An AST call node.

    Returns:
        bool: Whether this looks like a ``unittest.mock.patch`` invocation.
    """

    func = node.func

    if isinstance(func, ast.Name) and func.id == "patch":
        return True

    if (
        isinstance(func, ast.Attribute)
        and func.attr in ("patch", "object")
        and isinstance(func.value, ast.Name)
        and func.value.id == "patch"
    ):
        return True

    return False


def _extract_patch_targets(node: ast.Call) -> list[str]:
    """
    Extract the final dotted component from string args passed to ``patch``.

    Args:
        node (ast.Call): A ``patch`` / ``patch.object`` call node.

    Returns:
        list[str]: Last component of each string literal argument, e.g.
            ``["run_agent"]`` from ``patch("module.run_agent")``.
    """

    targets: list[str] = []

    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            targets.append(arg.value.split(".")[-1])

    for keyword in node.keywords:
        if isinstance(keyword.value, ast.Constant) and isinstance(
            keyword.value.value, str
        ):
            targets.append(keyword.value.value.split(".")[-1])

    return targets


def _collect_test_files(root: Path) -> list[Path]:
    """
    Return all ``test_*.py`` files under ``root``.

    Args:
        root (Path): Root test directory.

    Returns:
        list[Path]: Sorted list of test file paths.
    """

    return sorted(root.rglob("test_*.py"))


def build_test_index(root: Path) -> TestIndex:
    """
    Parse every test file and build a ``TestIndex`` of what is covered.

    Args:
        root (Path): Root of the ``tests/`` directory.

    Returns:
        TestIndex: Populated index of all test references.
    """

    index = TestIndex()

    for test_file in _collect_test_files(root):
        source_text: str = test_file.read_text(encoding="utf-8")
        tree: ast.Module = ast.parse(source_text, filename=str(test_file))

        # Collect test function names and test class names.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    index.function_names.add(node.name.lower())

            elif isinstance(node, ast.ClassDef):
                if node.name.startswith("Test"):
                    index.class_names.add(node.name.lower())

        # Collect imports from nominal_code.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("nominal_code"):
                    for alias in node.names:
                        index.imported_names.add(alias.asname or alias.name)

        # Collect called names and patch targets inside test function bodies.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            if not node.name.startswith("test_") and not node.name.startswith("_make_"):
                continue

            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue

                if _is_patch_call(child):
                    index.patched_strings.update(_extract_patch_targets(child))
                    continue

                func = child.func

                if isinstance(func, ast.Name):
                    index.called_names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    index.called_names.add(func.attr)

    return index


# ---------------------------------------------------------------------------
# Coverage check
# ---------------------------------------------------------------------------


def _name_convention_match(func: FunctionInfo, index: TestIndex) -> bool:
    """
    Return True if a test function name embeds the source function's bare name.

    Checks whether any test function name, when split by ``_``, contains
    the source function's bare name as a contiguous sub-sequence of tokens.

    Args:
        func (FunctionInfo): Source function metadata.
        index (TestIndex): Pre-built test index.

    Returns:
        bool: True if a test function name covers this function.
    """

    bare_lower: str = func.bare_name.lower()

    for test_name in index.function_names:
        # Remove the leading "test_" prefix then check for bare_name.
        remainder: str = test_name[5:]  # strip "test_"

        if remainder == bare_lower or remainder.startswith(bare_lower + "_"):
            return True

    return False


def _class_name_match(func: FunctionInfo, index: TestIndex) -> bool:
    """
    Return True if a test class name embeds the source function's bare name.

    Useful for cases where an entire method is exercised by the test class
    (e.g. ``TestEnsureReady`` covers ``ensure_ready``).

    Args:
        func (FunctionInfo): Source function metadata.
        index (TestIndex): Pre-built test index.

    Returns:
        bool: True if a test class name covers this function.
    """

    bare_lower: str = func.bare_name.lower()
    # Also match without underscores so e.g. ``auto_trigger_job`` matches
    # ``TestAutoTriggerJob`` → ``autotriggerjob``.
    bare_no_underscores: str = bare_lower.replace("_", "")

    for class_name in index.class_names:
        # Strip "test" prefix, lowercase compare.
        remainder: str = class_name[4:]  # strip "test"

        if bare_lower in remainder or bare_no_underscores in remainder:
            return True

    return False


def _direct_reference_match(func: FunctionInfo, index: TestIndex) -> bool:
    """
    Return True if the function's bare name appears in calls or imports.

    Args:
        func (FunctionInfo): Source function metadata.
        index (TestIndex): Pre-built test index.

    Returns:
        bool: True if the function is directly called or imported in tests.
    """

    return (
        func.name in index.called_names
        or func.bare_name in index.called_names
        or func.name in index.patched_strings
        or func.bare_name in index.patched_strings
        or func.name in index.imported_names
        or func.bare_name in index.imported_names
    )


def is_tested(func: FunctionInfo, index: TestIndex) -> bool:
    """
    Return True if the function is covered by at least one test heuristic.

    Args:
        func (FunctionInfo): Source function metadata.
        index (TestIndex): Pre-built test index.

    Returns:
        bool: True if the function is considered tested.
    """

    return (
        _name_convention_match(func, index)
        or _class_name_match(func, index)
        or _direct_reference_match(func, index)
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _relative(path: Path, base: Path) -> str:
    """
    Return ``path`` relative to ``base``, falling back to the absolute path.

    Args:
        path (Path): The path to make relative.
        base (Path): The base directory.

    Returns:
        str: Relative path string.
    """

    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def print_report(
    functions: list[FunctionInfo],
    index: TestIndex,
    base: Path,
) -> int:
    """
    Print an untested-function report to stdout and return the exit code.

    Args:
        functions (list[FunctionInfo]): All source functions.
        index (TestIndex): Pre-built test index.
        base (Path): Base path for relative display.

    Returns:
        int: ``0`` if everything is tested, ``1`` if gaps exist.
    """

    untested: list[FunctionInfo] = [
        func for func in functions if not is_tested(func, index)
    ]

    total: int = len(functions)
    gap: int = len(untested)
    tested_count: int = total - gap

    print(f"\nCoverage scan: {tested_count}/{total} functions appear tested.\n")

    if not untested:
        print("No untested functions found.")

        return 0

    # Group by file for readability.
    by_file: dict[Path, list[FunctionInfo]] = {}

    for func in untested:
        by_file.setdefault(func.file, []).append(func)

    print(f"Untested functions ({gap}):\n")

    for source_file, funcs in sorted(by_file.items()):
        rel: str = _relative(source_file, base)
        print(f"  {rel}")

        for func in sorted(funcs, key=lambda f: f.line):
            print(f"    L{func.line:<5} {func.qualified_name}")

        print()

    return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Run the coverage scan and exit with code 0 (clean) or 1 (gaps found).
    """

    base: Path = Path(__file__).parent

    functions: list[FunctionInfo] = extract_source_functions(SOURCE_ROOT)
    index: TestIndex = build_test_index(TEST_ROOT)

    exit_code: int = print_report(functions, index, base)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
