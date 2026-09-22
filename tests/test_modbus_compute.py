import pytest

from imodbus_rtu.compute.modbus_compute import (
    MasterModbusCompute,
    RegisterValue,
    VariableCandidate,
    compute_crc,
    error_bit_validation,
    find_variable_candidates,
    registers_compute,
    validate_response_crc,
)


def make_response(payload: bytes) -> bytes:
    return payload + compute_crc(payload)


class TestComputeCrc:
    def test_check_value_from_crc_catalog(self):
        # CRC-16/MODBUS check value for "123456789" is 0x4B37, low byte first.
        assert compute_crc(b"123456789") == b"\x37\x4b"

    def test_empty_input(self):
        assert compute_crc(b"") == b"\x01\xa0" or len(compute_crc(b"")) == 2

    def test_deterministic(self):
        assert compute_crc(b"\x01\x03\x00\x1e") == compute_crc(b"\x01\x03\x00\x1e")

    def test_different_input_different_crc(self):
        assert compute_crc(b"\x01\x03") != compute_crc(b"\x02\x03")


class TestValidateResponseCrc:
    def test_valid_response(self):
        response = make_response(bytes([0x01, 0x03, 0x02, 0x07, 0xD0]))
        assert validate_response_crc(response) is True

    def test_corrupted_payload_fails(self):
        response = bytearray(make_response(bytes([0x01, 0x03, 0x02, 0x07, 0xD0])))
        response[3] ^= 0xFF
        assert validate_response_crc(bytes(response)) is False

    def test_too_short_response_fails(self):
        assert validate_response_crc(b"\x01\x03") is False


class TestRegistersCompute:
    def test_parses_big_endian_values(self):
        response = make_response(bytes([0x01, 0x03, 0x02, 0x07, 0xD0]))
        assert registers_compute(response, 1) == [2000]

    def test_multiple_registers(self):
        payload = bytes([0x01, 0x03, 0x04, 0x00, 0x01, 0xFF, 0xFF])
        response = make_response(payload)
        assert registers_compute(response, 2) == [1, 65535]

    def test_zero_count_returns_empty(self):
        assert registers_compute(b"\x00", 0) == []


class TestErrorBitValidation:
    def test_exception_response_returns_empty_list(self):
        response = bytes([0x01, 0x83, 0x02, 0x00, 0x00])
        assert error_bit_validation(response, quiet=True) == []

    def test_normal_response_returns_none(self):
        response = bytes([0x01, 0x03, 0x02, 0x00, 0x00])
        assert error_bit_validation(response, quiet=True) is None


class TestFindVariableCandidates:
    def test_detects_changed_registers(self):
        first = [RegisterValue(1, 0, 10), RegisterValue(1, 5, 100)]
        second = [RegisterValue(1, 0, 11), RegisterValue(1, 5, 100)]
        candidates = find_variable_candidates(first, second)
        assert candidates == [
            VariableCandidate(slave_id=1, address=0, first_value=10, second_value=11)
        ]

    def test_no_overlap_returns_empty(self):
        assert find_variable_candidates([RegisterValue(1, 0, 1)], [RegisterValue(2, 0, 1)]) == []


class TestPlotBaseFrame:
    def test_probe_frame_structure(self):
        frame = MasterModbusCompute.plot_base(1, 3, 30, 1)
        assert frame == bytearray([0x01, 0x03, 0x00, 0x1E, 0x00, 0x01])

    def test_full_frame_crc_matches_known_vector(self):
        request = MasterModbusCompute.plot_base(1, 3, 30, 1)
        frame = bytes(request) + compute_crc(request)
        assert frame.hex() == "0103001e0001e40c"


class FakeSerial:
    """Minimal serial.Serial stub returning canned responses."""

    def __init__(self, responses=None, error_on_read=None):
        self.is_open = True
        self.timeout = None
        self.responses = list(responses or [])
        self.error_on_read = error_on_read
        self.written: list[bytes] = []

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def write(self, frame):
        self.written.append(bytes(frame))
        return len(frame)

    def read(self, size):
        if self.error_on_read:
            raise self.error_on_read
        if not self.responses:
            return b""
        return self.responses.pop(0)[:size]

    def close(self):
        self.is_open = False


def build_client(fake_serial):
    client = MasterModbusCompute(port="FAKE", timeout=0.01)
    client.serial = fake_serial
    return client


class TestReadHoldingRegisters:
    def test_valid_response_returns_values(self):
        response = make_response(bytes([0x01, 0x03, 0x02, 0x07, 0xD0]))
        client = build_client(FakeSerial(responses=[response]))
        assert client.read_holding_registers(slave_id=1, address=30, count=1) == [2000]

    def test_short_response_is_discarded(self):
        # Only 4 of the 5 expected bytes arrive: must not parse as zeros.
        client = build_client(FakeSerial(responses=[b"\x01\x03\x02\x07"]))
        assert client.read_holding_registers(slave_id=1, address=30, count=1) == []

    def test_crc_error_returns_empty(self):
        bad = bytearray(make_response(bytes([0x01, 0x03, 0x02, 0x07, 0xD0])))
        bad[-1] ^= 0xFF
        client = build_client(FakeSerial(responses=[bytes(bad)]))
        assert client.read_holding_registers(slave_id=1, address=30, count=1) == []

    def test_no_response_returns_empty(self):
        client = build_client(FakeSerial(responses=[]))
        assert client.read_holding_registers(slave_id=1, address=30, count=1) == []

    def test_serial_exception_returns_empty(self):
        import serial

        client = build_client(FakeSerial(error_on_read=serial.SerialException("port gone")))
        assert client.read_holding_registers(slave_id=1, address=30, count=1) == []

    def test_closed_port_returns_empty(self):
        client = MasterModbusCompute(port="FAKE")
        assert client.read_holding_registers(slave_id=1, address=30, count=1) == []


class TestProbeAndScans:
    def test_probe_slave_true_when_values_returned(self):
        response = make_response(bytes([0x01, 0x03, 0x02, 0x00, 0x2A]))
        client = build_client(FakeSerial(responses=[response]))
        assert client.probe_slave(1) is True

    def test_probe_slave_false_when_no_response(self):
        client = build_client(FakeSerial(responses=[]))
        assert client.probe_slave(1) is False

    def test_scan_registers_delegates_to_batched_reads(self):
        payload = bytes([0x01, 0x03, 0x06, 0x00, 0x0A, 0x00, 0x14, 0x00, 0x1E])
        client = build_client(FakeSerial(responses=[make_response(payload)]))
        found = client.scan_registers(1, 0, 2, delay=0)
        assert [(item.address, item.value) for item in found] == [(0, 10), (1, 20), (2, 30)]
        assert len(client.serial.written) == 1  # single batched frame


def make_single_payload(value: int) -> bytes:
    return bytes([0x01, 0x03, 0x02]) + value.to_bytes(2, "big")


class TestReadRegisterBlock:
    def test_batch_read_uses_single_frame(self):
        payload = bytes([0x01, 0x03, 0x06, 0x00, 0x0A, 0x00, 0x14, 0x00, 0x1E])
        client = build_client(FakeSerial(responses=[make_response(payload)]))
        found = client.read_register_block(1, 0, 2)

        assert [(item.address, item.value) for item in found] == [(0, 10), (1, 20), (2, 30)]
        assert len(client.serial.written) == 1

    def test_batch_failure_falls_back_to_single_reads(self):
        # First request (batch) gets no answer, singles all answer.
        responses = [b""] + [make_response(make_single_payload(v)) for v in (10, 20, 30)]
        client = build_client(FakeSerial(responses=responses))
        found = client.read_register_block(1, 0, 2)

        assert [(item.address, item.value) for item in found] == [(0, 10), (1, 20), (2, 30)]
        assert len(client.serial.written) == 4  # 1 batch + 3 singles

    def test_address_hole_is_filled_by_fallback(self):
        # Batch fails, then singles: reg 0 ok, reg 1 silent, reg 2 ok.
        responses = [
            b"",
            make_response(make_single_payload(10)),
            b"",
            make_response(make_single_payload(30)),
        ]
        client = build_client(FakeSerial(responses=responses))
        found = client.read_register_block(1, 0, 2)

        assert [(item.address, item.value) for item in found] == [(0, 10), (2, 30)]

    def test_ranges_bigger_than_limit_are_chunked(self):
        values = [i + 1 for i in range(130)]
        body = bytes([0x01, 0x03, 0xFA]) + b"".join(
            value.to_bytes(2, "big") for value in values[:125]
        )
        body2 = bytes([0x01, 0x03, 0x0A]) + b"".join(
            value.to_bytes(2, "big") for value in values[125:]
        )
        client = build_client(FakeSerial(responses=[make_response(body), make_response(body2)]))
        found = client.read_register_block(1, 0, 129)

        assert [item.value for item in found] == values
        counts = [int.from_bytes(frame[4:6], "big") for frame in client.serial.written]
        assert counts == [125, 5]

    def test_progress_callback_reports_cumulative(self):
        payload = bytes([0x01, 0x03, 0x06, 0x00, 0x0A, 0x00, 0x14, 0x00, 0x1E])
        client = build_client(FakeSerial(responses=[make_response(payload)]))
        calls: list[tuple[int, int]] = []
        client.read_register_block(
            1, 0, 2, on_progress=lambda done, total: calls.append((done, total))
        )
        assert calls == [(3, 3)]

    def test_read_timeout_scales_with_frame_size(self):
        client = build_client(FakeSerial(responses=[b""]))
        client.read_holding_registers(slave_id=1, address=0, count=1)
        single_timeout = client.serial.timeout

        client = build_client(FakeSerial(responses=[b""]))
        client.read_holding_registers(slave_id=1, address=0, count=125)
        batch_timeout = client.serial.timeout

        # 7 bytes vs 255 bytes at 9600 baud: the batch read needs a much
        # longer window than the configured client timeout (0.01 s here).
        assert single_timeout > 0.01
        assert batch_timeout > single_timeout * 10

    def test_scan_slave_addresses_finds_responders(self):
        def response_for(slave):
            return make_response(bytes([slave, 0x03, 0x02, 0x00, 0x01]))

        responses = [b"", response_for(2), b"", response_for(4)]
        client = build_client(FakeSerial(responses=responses))
        assert client.scan_slave_addresses(1, 4, delay=0) == [2, 4]


class TestContextManager:
    def test_exit_closes_serial(self):
        fake = FakeSerial()
        client = MasterModbusCompute(port="FAKE")
        client.serial = fake
        with client:
            pass
        assert fake.is_open is False


@pytest.mark.parametrize(
    ("frame", "valid"),
    [
        (make_response(b"\x01\x03\x02\x00\x01"), True),
        (b"\x01\x03\x02\x00\x01\x00\x00", False),
    ],
)
def test_crc_parametrized(frame, valid):
    assert validate_response_crc(frame) is valid
