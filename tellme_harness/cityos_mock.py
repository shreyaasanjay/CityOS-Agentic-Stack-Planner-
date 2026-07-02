"""Mock CityOS client backed by structured JSON fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import CityOSContextObject


class MockCityOSClient:
    def __init__(self, fixture_path: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.fixture_path = Path(fixture_path) if fixture_path else base_dir / "cityos_mock_data" / "cityos_context_examples.json"
        self._context_objects = self._load_fixture()

    def _load_fixture(self) -> list[CityOSContextObject]:
        try:
            raw_text = self.fixture_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"CityOS mock fixture not found at {self.fixture_path}. "
                "V0 requires cityos_mock_data/cityos_context_examples.json."
            ) from exc
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CityOS mock fixture at {self.fixture_path} is not valid JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise ValueError(
                f"CityOS mock fixture at {self.fixture_path} must be a JSON array of context objects."
            )
        return [CityOSContextObject(**item) for item in payload]

    def available_tools(self) -> list[str]:
        return [
            "get_occupancy_context",
            "get_motion_context",
            "get_audio_context",
            "get_room_state",
            "cityos_context_lookup",
        ]

    def preview_context(
        self,
        space_id: str | None = None,
        context_type: str | None = None,
        timestamp: str | None = None,
        limit: int = 3,
    ) -> list[CityOSContextObject]:
        return self._find_matches(space_id=space_id, context_type=context_type, timestamp=timestamp)[:limit]

    def get_occupancy_context(self, space_id: str | None, timestamp: str | None = None) -> dict:
        return self._select_one(space_id=space_id, context_type="occupancy", timestamp=timestamp).model_dump()

    def get_motion_context(self, space_id: str | None, timestamp: str | None = None) -> dict:
        return self._select_one(space_id=space_id, context_type="motion", timestamp=timestamp).model_dump()

    def get_audio_context(self, space_id: str | None, timestamp: str | None = None) -> dict:
        return self._select_one(space_id=space_id, context_type="audio", timestamp=timestamp).model_dump()

    def get_room_state(self, space_id: str | None, timestamp: str | None = None) -> dict:
        return self._select_one(space_id=space_id, context_type="room_state", timestamp=timestamp).model_dump()

    def cityos_context_lookup(self, space_id: str | None, query: str, timestamp: str | None = None) -> dict:
        lowered = query.lower()
        context_type = None
        if "motion" in lowered:
            context_type = "motion"
        elif any(token in lowered for token in ("noise", "audio", "sound")):
            context_type = "audio"
        elif any(token in lowered for token in ("occupied", "empty", "occupancy", "people")):
            context_type = "occupancy"
        elif "state" in lowered:
            context_type = "room_state"
        return self._select_one(space_id=space_id, context_type=context_type, timestamp=timestamp).model_dump()

    def _select_one(
        self,
        space_id: str | None = None,
        context_type: str | None = None,
        timestamp: str | None = None,
    ) -> CityOSContextObject:
        matches = self._find_matches(space_id=space_id, context_type=context_type, timestamp=timestamp)
        if not matches:
            raise LookupError(f"No mock CityOS context found for context_type={context_type!r} timestamp={timestamp!r}")
        return matches[0]

    def _find_matches(
        self,
        space_id: str | None = None,
        context_type: str | None = None,
        timestamp: str | None = None,
    ) -> list[CityOSContextObject]:
        matches = list(self._context_objects)
        if space_id:
            matches = [item for item in matches if item.space_id == space_id]
        if context_type:
            matches = [item for item in matches if item.context_type == context_type]
        if timestamp:
            exact = [item for item in matches if timestamp in item.timestamp or item.value.get("observed_at") == timestamp]
            if exact:
                return sorted(exact, key=lambda item: item.timestamp, reverse=True)
        return sorted(matches, key=lambda item: item.timestamp, reverse=True)
