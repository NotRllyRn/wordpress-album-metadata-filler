from __future__ import annotations


def format_dry_run_row(post_id: int, reasons: list[str], acf_updates: dict) -> str:
    return f"post={post_id} reasons={','.join(reasons)} updates={sorted(acf_updates.keys())}"


def summarize_counts(rows: list[tuple[str, int]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for status, _post_id in rows:
        summary[status] = summary.get(status, 0) + 1
    return summary
