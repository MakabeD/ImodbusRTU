"""Progress bar accounting for multi-chunk register scans."""

import click

from imodbus_rtu.cli import analyze_registers, render_progress_bar


class _ChunkedClient:
    """Emits the cumulative progress contract of read_register_block."""

    def read_register_block(self, slave_id, register_start, register_end, on_progress=None):
        total = register_end - register_start + 1
        processed = 0
        while processed < total:
            processed = min(processed + 125, total)
            if on_progress is not None:
                on_progress(processed, total)
        return []


def test_render_progress_bar_clamps_overshoot(capsys):
    render_progress_bar(255, 130, prefix="Progreso: ", width=10)
    out = capsys.readouterr().out
    assert "[" + "█" * 10 + "]" in out
    assert "130/130 (100%)" in out
    assert "196%" not in out
    assert "255/130" not in out


def test_analyze_registers_does_not_double_count_chunks(monkeypatch):
    lines: list[str] = []

    def capture(message="", nl=True, **kwargs):
        lines.append(message)

    monkeypatch.setattr(click, "echo", capture)
    analyze_registers(
        _ChunkedClient(),
        slave_ids=[1],
        register_start=0,
        register_end=129,
        show_progress=True,
    )
    progress = [line for line in lines if line.startswith("Progreso:")]
    assert progress, lines
    assert "130/130 (100%)" in progress[-1]
    assert "255/130" not in progress[-1]
    assert "196%" not in progress[-1]
