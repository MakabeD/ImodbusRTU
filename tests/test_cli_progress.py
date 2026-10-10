import pytest

from imodbus_rtu.cli import analyze_registers, render_progress_bar


class ChunkedProgressClient:
    def read_register_block(self, slave_id, register_start, register_end, on_progress=None):
        assert (register_start, register_end) == (0, 129)
        assert on_progress is not None
        on_progress(125, 130)
        on_progress(130, 130)
        return []


@pytest.mark.parametrize("slave_ids", [[1], [1, 2]])
def test_analyze_registers_keeps_chunk_progress_within_total(slave_ids, capsys):
    total_registers = 130 * len(slave_ids)

    analyze_registers(
        client=ChunkedProgressClient(),
        slave_ids=slave_ids,
        register_start=0,
        register_end=129,
        show_progress=True,
    )

    output = capsys.readouterr().out
    assert f"{total_registers}/{total_registers} (100%)" in output
    assert f"{255 * len(slave_ids)}/{total_registers}" not in output


def test_render_progress_bar_clamps_values_above_total(capsys):
    render_progress_bar(current=255, total=130, width=5)

    output = capsys.readouterr().out
    assert "[█████] 130/130 (100%)" in output
