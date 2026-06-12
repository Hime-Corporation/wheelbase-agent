"""complete_demand_matrix_setup — signal that demand-matrix setup is done.

Go-API write tool: POST /demand-matrix/complete
Signals the Wheelbase backend that setup is finished; the workspace transitions
out of demand-matrix setup mode.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def complete_demand_matrix_setup(args: dict, **kwargs) -> str:  # noqa: ARG001
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()

    try:
        result = client.go_api("POST", "/demand-matrix/complete", body={})
        return ok(
            result if result is not None else {"kind": "complete_demand_matrix_setup"}
        )
    except Exception as exc:  # noqa: BLE001
        return err(f"complete_demand_matrix_setup failed: {exc}")
    finally:
        client.close()
