import json

from wheelbase_sdk.errors import WheelbaseAuthError, signed_out_result, ok, err


def test_signed_out_result():
    d = json.loads(signed_out_result())
    assert d["error"] == "not_signed_in"
    assert "Sign in" in d["message"]


def test_ok_serializes():
    assert json.loads(ok({"a": 1})) == {"a": 1}


def test_ok_handles_non_serializable_via_default():
    # default=str keeps it from raising on odd types
    assert json.loads(ok({"x": {1, 2}})) is not None


def test_err_has_message_and_extra():
    d = json.loads(err("boom", status=502))
    assert d["error"] == "boom"
    assert d["status"] == 502


def test_auth_error_is_exception():
    assert issubclass(WheelbaseAuthError, Exception)
