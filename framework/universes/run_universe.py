import argparse
import json
from pathlib import Path
from framework.universes.data_models import BaseUniverseParams
from framework.universes.base_universe import run_app

def main():
    parser = argparse.ArgumentParser(description="Run a universe from parameters JSON file")
    parser.add_argument("--params-file", required=True, help="Path to JSON file containing BaseUniverseParams")
    parser.add_argument("--status-file", required=False, help="Path to write status JSON (optional)")
    parser.add_argument("--host", default=None, help="Override host (optional)")
    parser.add_argument("--port", type=int, default=None, help="Override port (optional)")
    parser.add_argument("--cors", nargs='*', default=None, help="CORS origins list (optional)")
    args = parser.parse_args()

    # Load parameters from JSON file
    with open(args.params_file, "r", encoding="utf-8") as f:
        params_json = json.load(f)

    # Recreate BaseUniverseParams instance (pydantic model)
    if hasattr(BaseUniverseParams, "model_validate"):
        params = BaseUniverseParams.model_validate(params_json)
    else:
        params = BaseUniverseParams(**params_json)

    # Write initial status if a status file is provided
    if args.status_file:
        try:
            Path(args.status_file).write_text(json.dumps({"status": "starting"}), encoding="utf-8")
        except Exception:
            pass

    # Only override params with CLI args if they are explicitly provided (not None)
    host = args.host if args.host is not None else (params.info.host if params.info else "127.0.0.1")
    port = args.port if args.port is not None else (params.info.port if params.info else 0)
    cors = args.cors if args.cors is not None else None

    # Run the FastAPI app for the universe, passing the status_file
    run_app(
        params=params,
        host=host,
        port=port,
        cors=cors,
        status_file=args.status_file,
    )

if __name__ == "__main__":
    main()
