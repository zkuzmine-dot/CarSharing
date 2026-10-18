"""Pure business calculations with dependencies supplied explicitly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from carsharing.models import Tariff


class FarePeriod(Enum):
    DAY = "day"
    NIGHT = "night"


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
class TripPricing:
    started_at: datetime
    finished_at: datetime
    distance: float
    idle_minutes: int
    demand_coefficient: float
    promo_code: str | None
    loyalty_points: int
    use_loyalty: bool


@dataclass(frozen=True)
class TripCostCalculation:
    amount: float
    remaining_points: int
    earned_points: int


def calculate_cost(
    tariff: Tariff,
    period: FarePeriod,
    minutes: int,
    distance: float,
    idle_minutes: int,
) -> float:
    """Calculate a fare for day or night using one shared formula."""
    coefficient = (
        tariff.night_coefficient if period is FarePeriod.NIGHT else 1.0
    )
    driving = (minutes * tariff.per_minute + distance * tariff.per_km) * coefficient
    parking = idle_minutes * tariff.parking_per_minute * coefficient
    return round(driving + parking, 2)


def calculate_cancellation_fee(
    created_at: datetime,
    expires_at: datetime,
    now: datetime,
    free_minutes: int,
    cancellation_fee: float,
    is_active: bool,
) -> CancellationCalculation:
    """Describe the cancellation outcome without mutation or clock access."""
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
    """Calculate discounted amount and resulting points without mutation."""
    remaining_points = loyalty_points
    if use_loyalty:
        discount = min(
            loyalty_points * discount_per_point,
            maximum_discount,
            amount,
        )
        amount -= discount
        remaining_points -= int(discount)
    return LoyaltyCalculation(amount, remaining_points, int(amount // 100))


def apply_promo_discount(
    amount: float,
    promo_code: str | None,
    started_at: datetime,
    night_start_hour: int,
    night_end_hour: int,
) -> float:
    """Apply a promotional code using explicit night-hour boundaries."""
    if promo_code == "WELCOME10":
        return amount * 0.9
    if promo_code == "NIGHT20" and (
        started_at.hour >= night_start_hour or started_at.hour < night_end_hour
    ):
        return amount * 0.8
    return amount


def calculate_trip_cost(
    tariff: Tariff,
    pricing: TripPricing,
    night_start_hour: int,
    night_end_hour: int,
    loyalty_discount_per_point: float,
    maximum_loyalty_discount: float,
) -> TripCostCalculation:
    """Calculate a complete trip cost from explicit immutable values."""
    minutes = max(
        1, int((pricing.finished_at - pricing.started_at).total_seconds() // 60)
    )
    period = (
        FarePeriod.NIGHT
        if pricing.finished_at.hour >= night_start_hour
        or pricing.finished_at.hour < night_end_hour
        else FarePeriod.DAY
    )
    amount = calculate_cost(
        tariff, period, minutes, pricing.distance, pricing.idle_minutes
    )
    amount *= pricing.demand_coefficient
    amount = apply_promo_discount(
        amount,
        pricing.promo_code,
        pricing.started_at,
        night_start_hour,
        night_end_hour,
    )
    loyalty = apply_loyalty_discount(
        amount,
        pricing.loyalty_points,
        pricing.use_loyalty,
        loyalty_discount_per_point,
        maximum_loyalty_discount,
    )
    return TripCostCalculation(
        round(loyalty.amount, 2),
        loyalty.remaining_points,
        loyalty.earned_points,
    )


def calculate_fine(is_parking_allowed: bool, fine_amount: float) -> float:
    """Calculate a parking fine without mutating trip or customer."""
    return 0.0 if is_parking_allowed else fine_amount
