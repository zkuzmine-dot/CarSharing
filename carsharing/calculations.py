"""Pure business calculations with time, policy, and model data passed explicitly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from carsharing.models import Tariff


@dataclass(frozen=True)
class CancellationCalculation:
    status: str
    fee: float


@dataclass(frozen=True)
class LoyaltyCalculation:
    amount: float
    remaining_points: int
    earned_points: int


@dataclass(frozen=True)
class TripCostCalculation:
    amount: float
    remaining_points: int
    earned_points: int


def calculate_cancellation_fee(
    created_at: datetime,
    expires_at: datetime,
    now: datetime,
    free_minutes: int,
    cancellation_fee: float,
    is_active: bool,
) -> CancellationCalculation:
    """Describe a cancellation outcome without reading clocks or changing objects."""
    if not is_active:
        return CancellationCalculation("inactive", 0.0)
    if now > expires_at:
        return CancellationCalculation("expired", 0.0)
    elapsed_minutes = (now - created_at).total_seconds() / 60
    fee = cancellation_fee if elapsed_minutes > free_minutes else 0.0
    return CancellationCalculation("cancelled", fee)


def apply_loyalty_discount(
    amount: float,
    loyalty_points: int,
    use_loyalty: bool,
    discount_per_point: float,
    maximum_discount: float,
) -> LoyaltyCalculation:
    """Calculate discounted amount and point balance without mutating a customer."""
    remaining_points = loyalty_points
    if use_loyalty:
        discount = min(
            loyalty_points * discount_per_point,
            maximum_discount,
            amount,
        )
        amount -= discount
        remaining_points -= int(discount)
    earned_points = int(amount // 100)
    return LoyaltyCalculation(amount, remaining_points, earned_points)


def apply_promo_discount(
    amount: float,
    promo_code: str | None,
    started_at: datetime,
    night_start_hour: int,
    night_end_hour: int,
) -> float:
    """Apply a supported promo code using explicit night-hour boundaries."""
    if promo_code == "WELCOME10":
        return amount * 0.9
    if promo_code == "NIGHT20" and (
        started_at.hour >= night_start_hour or started_at.hour < night_end_hour
    ):
        return amount * 0.8
    return amount


def calculate_trip_cost(
    tariff: Tariff,
    started_at: datetime,
    finished_at: datetime,
    distance: float,
    idle_minutes: int,
    demand_coefficient: float,
    promo_code: str | None,
    loyalty_points: int,
    use_loyalty: bool,
    night_start_hour: int,
    night_end_hour: int,
    loyalty_discount_per_point: float,
    maximum_loyalty_discount: float,
) -> TripCostCalculation:
    """Calculate a complete trip charge from explicit inputs only."""
    minutes = max(1, int((finished_at - started_at).total_seconds() // 60))
    night = (
        finished_at.hour >= night_start_hour or finished_at.hour < night_end_hour
    )
    if night:
        amount = tariff.calc_night_cost(minutes, distance, idle_minutes)
    else:
        amount = tariff.calc_day_cost(minutes, distance, idle_minutes)
    amount *= demand_coefficient
    amount = apply_promo_discount(
        amount, promo_code, started_at, night_start_hour, night_end_hour
    )
    loyalty = apply_loyalty_discount(
        amount,
        loyalty_points,
        use_loyalty,
        loyalty_discount_per_point,
        maximum_loyalty_discount,
    )
    return TripCostCalculation(
        round(loyalty.amount, 2),
        loyalty.remaining_points,
        loyalty.earned_points,
    )


def calculate_fine(is_parking_allowed: bool, fine_amount: float) -> float:
    """Calculate a parking fine without changing the trip or customer."""
    return 0.0 if is_parking_allowed else fine_amount
