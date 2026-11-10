"""Time-based position model. Home Assistant uses 100 for fully open."""

from dataclasses import dataclass

ENDPOINT_MARGIN_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class Move:
    """An estimate derived from a command and measured full travel time."""

    start: float | None
    target: int
    started_at: float
    full_seconds: float

    @property
    def raising(self) -> bool:
        return self.target == 100 or (self.start is not None and self.target > self.start)

    @property
    def seconds(self) -> float:
        if self.target in (0, 100):
            # Give the motor enough time to reach the physical limit even when
            # the estimate has drifted. At a limit the motor stops on its own.
            return self.full_seconds + ENDPOINT_MARGIN_SECONDS
        assert self.start is not None
        return abs(self.target - self.start) * self.full_seconds / 100

    def estimate(self, now: float) -> float | None:
        if self.start is None:
            return None
        distance = max(0.0, now - self.started_at) * 100 / self.full_seconds
        if self.raising:
            return min(self.start + distance, self.target)
        return max(self.start - distance, self.target)
