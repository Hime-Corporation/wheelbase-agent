"""assess_runlist — score all cars in a runlist via the backend AI service.

Calls POST /v1/ai/imx/runlist which persists per-car IMX scores to
runlist_car_imx_score and returns the full scoring output.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def assess_runlist(args: dict, **kwargs) -> str:
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId must be a non-empty UUID string")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {"runlistId": runlist_id}
        provider = args.get("provider")
        if provider:
            body["provider"] = str(provider)
        mode = args.get("mode")
        if mode:
            body["mode"] = str(mode)

        result = client.go_api("POST", "/v1/ai/imx/runlist", body=body)
        return ok(result)
    except Exception as e:  # noqa: BLE001
        return err(f"assess_runlist failed: {e}")
    finally:
        client.close()
