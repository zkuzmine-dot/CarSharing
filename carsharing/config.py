"""Immutable application policy supplied to the service explicitly."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    cancellation_free_minutes: int = 5
    booking_duration_minutes: int = 15
    cancellation_fee: float = 120.0
    parking_fine: float = 1500.0
    demand_minimum: float = 0.9
    demand_maximum: float = 1.3
    night_start_hour: int = 23
    night_end_hour: int = 6
    loyalty_discount_per_point: float = 1.0
    maximum_loyalty_discount: float = 500.0
