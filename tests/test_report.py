from post_to_album.report import format_dry_run_row, summarize_counts


def test_format_dry_run_row_shows_post_id_reason_and_fields():
    row = format_dry_run_row(99, ["field-diff"], {"music_total_tracks": 3})

    assert "post=99" in row
    assert "field-diff" in row
    assert "music_total_tracks" in row


def test_summarize_counts_groups_statuses():
    summary = summarize_counts([("updated", 1), ("updated", 2), ("skipped", 3)])

    assert summary == {"updated": 2, "skipped": 1}
