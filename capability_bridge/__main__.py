"""CLI entry point for the external capability bridge."""

from __future__ import annotations

import json
import logging
import sys
from typing import Optional

from .bridge import CapabilityBridge
from .config import BridgeConfigError, load_bridge_config


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load_bridge_config(argv)
        result = CapabilityBridge(config).run()
    except (BridgeConfigError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1
    print(
        json.dumps(
            {
                "snapshot_id": result.snapshot_id,
                "space_id": result.space_id,
                "schema_version": result.schema_version,
                "capability_count": result.capability_count,
                "dry_run": result.dry_run,
                "status_update_count": result.status_update_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
