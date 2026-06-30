"""generate_demand_score — rank inventory via the backend AI demand-matrix service.

Calls POST /v1/ai/rank/matrix which ranks vehicles from structured demand signals
using the tenant's configured demand categories. Backend request body shape
(services.RankByDemandMatrixInput):
  topK              int               -- number of top vehicles to return (default 50)
  provider          string            -- embedding provider ("gemini", etc.)
  mode              string            -- "hybrid" | "sql" | etc.
  categoryOverrides []{ key, current} -- optional per-category current-count overrides
  minGapRatio       float             -- minimum gap ratio filter (default 0.1)
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def generate_demand_score(args: dict, **kwargs) -> str:
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {}

        raw_top_k = args.get("topK")
        if raw_top_k is not None:
            body["topK"] = int(raw_top_k)

        provider = args.get("provider")
        if provider:
            body["provider"] = str(provider)

        mode = args.get("mode")
        if mode:
            body["mode"] = str(mode)

        min_gap_ratio = args.get("minGapRatio")
        if min_gap_ratio is not None:
            body["minGapRatio"] = float(min_gap_ratio)

        category_overrides = args.get("categoryOverrides")
        if category_overrides is not None:
            body["categoryOverrides"] = category_overrides

        result = client.go_api("POST", "/v1/ai/rank/matrix", body=body)
        return ok(result)
    except Exception as e:  # noqa: BLE001
        return err(f"generate_demand_score failed: {e}")
    finally:
        client.close()
