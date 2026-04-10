# type: ignore
from nominal_code.models import (
    ChangedFile,
    DiffSide,
    FileStatus,
    ReviewFinding,
)
from nominal_code.review.diff import (
    annotate_diff,
    build_diff_index,
    build_effective_summary,
    filter_findings,
    parse_diff_lines,
)


class TestParseDiffLines:
    def test_parse_diff_lines_addition_lines_in_right(self):
        patch_text = "@@ -0,0 +1,3 @@\n+line one\n+line two\n+line three\n"

        result = parse_diff_lines(patch=patch_text)

        assert 1 in result[DiffSide.RIGHT]
        assert 2 in result[DiffSide.RIGHT]
        assert 3 in result[DiffSide.RIGHT]

    def test_parse_diff_lines_deletion_lines_in_left(self):
        patch_text = "@@ -1,2 +1,0 @@\n-removed line 1\n-removed line 2\n"

        result = parse_diff_lines(patch=patch_text)

        assert 1 in result[DiffSide.LEFT]
        assert 2 in result[DiffSide.LEFT]

    def test_parse_diff_lines_context_lines_in_both(self):
        patch_text = "@@ -5,3 +5,3 @@\n context one\n context two\n"

        result = parse_diff_lines(patch=patch_text)

        assert 5 in result[DiffSide.LEFT]
        assert 5 in result[DiffSide.RIGHT]

    def test_parse_diff_lines_empty_patch_returns_empty_sets(self):
        result = parse_diff_lines(patch="")

        assert result[DiffSide.LEFT] == set()
        assert result[DiffSide.RIGHT] == set()

    def test_parse_diff_lines_returns_both_sides(self):
        patch_text = "@@ -1,1 +1,1 @@\n-old\n+new\n"

        result = parse_diff_lines(patch=patch_text)

        assert DiffSide.LEFT in result
        assert DiffSide.RIGHT in result


class TestBuildDiffIndex:
    def test_build_diff_index_includes_files_with_patches(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,1 +1,1 @@\n-old\n+new\n",
            )
        ]

        result = build_diff_index(changed_files=changed_files)

        assert "src/main.py" in result

    def test_build_diff_index_excludes_files_without_patch(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.ADDED,
                patch="",
            )
        ]

        result = build_diff_index(changed_files=changed_files)

        assert "src/main.py" not in result

    def test_build_diff_index_empty_list_returns_empty_dict(self):
        result = build_diff_index(changed_files=[])

        assert result == {}

    def test_build_diff_index_maps_file_to_side_sets(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,1 +1,1 @@\n+new line\n",
            )
        ]

        result = build_diff_index(changed_files=changed_files)

        assert DiffSide.LEFT in result["a.py"]
        assert DiffSide.RIGHT in result["a.py"]


class TestFilterFindings:
    def test_filter_findings_keeps_valid_in_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/main.py", line=2, body="Issue here"),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert len(rejected) == 0

    def test_filter_findings_rejects_line_outside_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/main.py", line=100, body="Not in diff"),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1

    def test_filter_findings_rejects_file_not_in_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/other.py", line=5, body="Not in PR"),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1

    def test_filter_findings_splits_mixed(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/main.py", line=1, body="Valid"),
            ReviewFinding(file_path="src/main.py", line=999, body="Invalid line"),
            ReviewFinding(file_path="src/other.py", line=5, body="Invalid file"),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert valid[0].body == "Valid"
        assert len(rejected) == 2

    def test_filter_findings_empty_findings(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1 +1 @@\n+new",
            ),
        ]
        valid, rejected = filter_findings(findings=[], changed_files=changed_files)

        assert valid == []
        assert rejected == []

    def test_filter_findings_multiple_hunks(self):
        patch = (
            "@@ -1,3 +1,3 @@\n-old\n+new\n context\n context\n"
            "@@ -20,3 +20,4 @@\n context\n+added\n context\n context"
        )
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch=patch),
        ]
        findings = [
            ReviewFinding(file_path="a.py", line=1, body="In first hunk"),
            ReviewFinding(file_path="a.py", line=21, body="In second hunk"),
            ReviewFinding(file_path="a.py", line=10, body="Between hunks"),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 2
        assert len(rejected) == 1
        assert rejected[0].body == "Between hunks"

    def test_filter_findings_deletion_lines_on_left_side(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,2 @@\n context\n-deleted\n context",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="a.py",
                line=2,
                body="Deleted line comment",
                side=DiffSide.LEFT,
            ),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert len(rejected) == 0

    def test_filter_findings_rejects_left_finding_on_right_only_file(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.ADDED,
                patch="@@ -0,0 +1,3 @@\n+line one\n+line two\n+line three",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="a.py",
                line=1,
                body="No left side here",
                side=DiffSide.LEFT,
            ),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1

    def test_filter_findings_multiline_suggestion_fully_in_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,5 +1,5 @@\n context\n+line2\n+line3\n+line4\n context",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="src/main.py",
                line=4,
                body="Simplify",
                suggestion="simplified()",
                start_line=2,
            ),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert len(rejected) == 0

    def test_filter_findings_multiline_suggestion_partially_outside_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -10,3 +10,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="src/main.py",
                line=12,
                body="Simplify",
                suggestion="simplified()",
                start_line=8,
            ),
        ]
        valid, rejected = filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1


class TestBuildEffectiveSummary:
    def test_build_effective_summary_no_rejected(self):
        result = build_effective_summary(summary="All good", rejected_findings=[])

        assert result == "All good"

    def test_build_effective_summary_with_rejected(self):
        rejected = [
            ReviewFinding(file_path="src/other.py", line=5, body="Missing update"),
            ReviewFinding(file_path="src/utils.py", line=20, body="Stale reference"),
        ]
        result = build_effective_summary(
            summary="Found issues", rejected_findings=rejected
        )

        assert result.startswith("Found issues")
        assert "Additional notes" in result
        assert "not in diff" in result
        assert "**src/other.py:5**" in result
        assert "Missing update" in result
        assert "**src/utils.py:20**" in result
        assert "Stale reference" in result

    def test_build_effective_summary_single_rejected(self):
        rejected = [
            ReviewFinding(file_path="a.py", line=1, body="Needs change"),
        ]
        result = build_effective_summary(summary="Summary", rejected_findings=rejected)

        assert "**a.py:1**" in result
        assert "Needs change" in result


class TestAnnotateDiff:
    def test_empty_patch(self):
        assert annotate_diff("") == ""
        assert annotate_diff("   ") == ""

    def test_single_hunk(self):
        patch = (
            "@@ -10,4 +10,5 @@ def foo():\n"
            "     existing_line\n"
            "-    old_code\n"
            "+    new_code\n"
            "+    added_line\n"
            "     context_line\n"
        )
        result = annotate_diff(patch)

        lines = result.splitlines()
        assert lines[0] == "@@ -10,4 +10,5 @@ def foo():"
        assert lines[1] == " 10:    existing_line"
        assert lines[2] == "-11:    old_code"
        assert lines[3] == "+11:    new_code"
        assert lines[4] == "+12:    added_line"
        assert lines[5] == " 13:    context_line"

    def test_multiple_hunks(self):
        patch = (
            "@@ -5,3 +5,3 @@ class A:\n"
            "     line5\n"
            "-    old6\n"
            "+    new6\n"
            "     line7\n"
            "@@ -20,3 +20,3 @@ class B:\n"
            "     line20\n"
            "-    old21\n"
            "+    new21\n"
            "     line22\n"
        )
        result = annotate_diff(patch)

        assert "+6:    new6" in result
        assert "+21:    new21" in result

    def test_additions_only(self):
        patch = "@@ -0,0 +1,3 @@\n+line_one\n+line_two\n+line_three\n"
        result = annotate_diff(patch)

        lines = result.splitlines()
        assert lines[1] == "+1:line_one"
        assert lines[2] == "+2:line_two"
        assert lines[3] == "+3:line_three"

    def test_deletions_only(self):
        patch = "@@ -1,2 +1,0 @@\n-removed_a\n-removed_b\n"
        result = annotate_diff(patch)

        assert "-1:removed_a" in result
        assert "-2:removed_b" in result

    def test_preserves_indentation(self):
        patch = (
            "@@ -1,3 +1,3 @@\n"
            "     four_spaces\n"
            "-        eight_spaces\n"
            "+        new_eight_spaces\n"
            "     four_spaces_again\n"
        )
        result = annotate_diff(patch)

        assert " 1:    four_spaces" in result
        assert "-2:        eight_spaces" in result
        assert "+2:        new_eight_spaces" in result
