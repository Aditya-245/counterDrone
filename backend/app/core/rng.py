"""The single stochastic source of the simulation.

Everything random in `core/` goes through an object satisfying :class:`RngLike`.
That is either a plain ``numpy.random.Generator`` (the seeded source handed to
:func:`app.core.engine.step`) or a :class:`CountingRng` wrapping one, which keeps
a running tally of variates drawn so RNG consumption can be compared between two
runs as a determinism check.

Nothing here calls the global ``random`` module or the wall clock.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np

__all__ = ["RngLike", "CountingRng"]


@runtime_checkable
class RngLike(Protocol):
    """The subset of ``numpy.random.Generator`` that `core/` is allowed to use."""

    def random(self) -> float:
        """Uniform variate in ``[0, 1)``."""
        ...

    def uniform(self, low: float = 0.0, high: float = 1.0, size: int | None = None) -> Any:
        """Uniform variates."""
        ...

    def normal(self, loc: float = 0.0, scale: float = 1.0, size: Any = None) -> Any:
        """Normal variates."""
        ...

    def poisson(self, lam: float = 1.0, size: Any = None) -> Any:
        """Poisson variates (used by the radar clutter model)."""
        ...

    def integers(self, low: int, high: int | None = None, size: Any = None) -> Any:
        """Discrete uniform integers."""
        ...


class CountingRng:
    """Pass-through wrapper recording how many variates the shared RNG served.

    `WorldState.rng_draws` is a real accounting of RNG consumption; a drift in
    that number between two otherwise identical runs is a determinism bug.
    """

    __slots__ = ("_rng", "count")

    def __init__(self, rng: np.random.Generator) -> None:
        self._rng = rng
        self.count = 0

    def random(self) -> float:
        """Uniform [0, 1)."""
        self.count += 1
        return float(self._rng.random())

    def uniform(self, low: float = 0.0, high: float = 1.0, size: int | None = None) -> Any:
        """Uniform variates."""
        self.count += 1
        return self._rng.uniform(low, high, size)

    def normal(self, loc: float = 0.0, scale: float = 1.0, size: Any = None) -> Any:
        """Normal variates."""
        self.count += 1
        return self._rng.normal(loc, scale, size)

    def poisson(self, lam: float = 1.0, size: Any = None) -> Any:
        """Poisson variates."""
        self.count += 1
        return self._rng.poisson(lam, size)

    def integers(self, low: int, high: int | None = None, size: Any = None) -> Any:
        """Discrete uniform integers."""
        self.count += 1
        return self._rng.integers(low, high, size)
