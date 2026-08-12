"""Tests for OperatorVerifier — verification logic."""

import pytest
from unittest.mock import patch, MagicMock

from operator_mode.operator_verifier import OperatorVerifier
from operator_mode.operator_types import VerifySpec, VerifyMethod


class TestOperatorVerifier:

    def setup_method(self):
        self.v = OperatorVerifier()

    def test_verify_none_always_passes(self):
        spec = VerifySpec(VerifyMethod.NONE, expected="", timeout_ms=100)
        assert self.v.verify(spec, "VX-TEST") is True

    def test_verify_window_exists_found(self):
        spec = VerifySpec(VerifyMethod.WINDOW_EXISTS, expected="Chrome", timeout_ms=500)
        mock_state = MagicMock()
        mock_state.open_windows = ["Google Chrome - YouTube"]
        mock_state.active_window = ""
        with patch("operator_mode.operator_verifier.OperatorVerifier._check_window",
                   return_value=True):
            assert self.v.verify(spec, "VX-TEST") is True

    def test_verify_window_exists_not_found(self):
        spec = VerifySpec(VerifyMethod.WINDOW_EXISTS, expected="Notepad",
                          timeout_ms=200, retry_on_fail=False)
        with patch("operator_mode.operator_verifier.OperatorVerifier._check_window",
                   return_value=False):
            assert self.v.verify(spec, "VX-TEST") is False

    def test_verify_retry_on_fail(self):
        call_count = [0]
        spec = VerifySpec(VerifyMethod.WINDOW_EXISTS, expected="App",
                          timeout_ms=600, retry_on_fail=True)

        def _check_side_effect(s):
            call_count[0] += 1
            return call_count[0] >= 2  # succeeds on 2nd attempt

        with patch("operator_mode.operator_verifier.OperatorVerifier._check",
                   side_effect=_check_side_effect):
            result = self.v.verify(spec, "VX-TEST")
        assert result is True
        assert call_count[0] >= 2
