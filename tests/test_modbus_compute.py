import unittest

from compute.modbus_compute import RegisterValue
from compute.modbus_compute import find_variable_candidates
from compute.modbus_compute import registers_compute


class TestModbusCompute(unittest.TestCase):
    def test_registers_compute_reads_big_endian_values(self):
        response = bytes([1, 3, 4, 0, 10, 1, 244, 0, 0])
        self.assertEqual(registers_compute(response, 2), [10, 500])

    def test_find_variable_candidates_returns_only_changed_registers(self):
        first_snapshot = [
            RegisterValue(slave_id=1, address=0, value=100),
            RegisterValue(slave_id=1, address=1, value=200),
            RegisterValue(slave_id=2, address=0, value=300),
        ]
        second_snapshot = [
            RegisterValue(slave_id=1, address=0, value=100),
            RegisterValue(slave_id=1, address=1, value=260),
            RegisterValue(slave_id=2, address=0, value=310),
        ]

        candidates = find_variable_candidates(first_snapshot, second_snapshot)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].slave_id, 1)
        self.assertEqual(candidates[0].address, 1)
        self.assertEqual(candidates[0].first_value, 200)
        self.assertEqual(candidates[0].second_value, 260)
        self.assertEqual(candidates[1].slave_id, 2)
        self.assertEqual(candidates[1].address, 0)
        self.assertEqual(candidates[1].first_value, 300)
        self.assertEqual(candidates[1].second_value, 310)


if __name__ == "__main__":
    unittest.main()
