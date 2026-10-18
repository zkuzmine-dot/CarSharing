"""Immutable domain records used by the refactored in-memory application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    license_number: str
    balance: float = 0.0
    status: str = "active"
    loyalty_points: int = 0
    bookings: tuple[str, ...] = ()
    trips: tuple[str, ...] = ()


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    center: tuple[float, float]
    radius: float
    allows_parking: bool = True
    cars: tuple[str, ...] = ()

    def contains(self, point: tuple[float, float]) -> bool:
        dx = point[0] - self.center[0]
        dy = point[1] - self.center[1]
        return dx * dx + dy * dy <= self.radius * self.radius


@dataclass(frozen=True)
class Car:
    id: str
    model: str
    plate: str
    fuel_level: float
    mileage: float
    status: str
    zone_id: str
    trips: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tariff:
    id: str
    per_minute: float
    per_km: float
    parking_per_minute: float
    night_coefficient: float
    trips: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        tariff_id: str,
        per_minute: float,
        per_km: float,
        parking_per_minute: float,
        night_coefficient: float,
    ) -> Tariff:
        return cls(
            tariff_id,
            per_minute,
            per_km,
            parking_per_minute,
            night_coefficient,
        )


@dataclass(frozen=True)
class Booking:
    id: str
    customer_id: str
    car_id: str
    created_at: datetime
    expires_at: datetime
    status: str = "active"


@dataclass(frozen=True)
class Trip:
    id: str
    customer_id: str
    car_id: str
    tariff_id: str
    started_at: datetime
    finished_at: datetime | None = None
    distance: float = 0.0
    cost: float = 0.0
    status: str = "active"

    @classmethod
    def start(
        cls,
        trip_id: str,
        customer_id: str,
        car_id: str,
        tariff_id: str,
        started_at: datetime,
    ) -> Trip:
        return cls(trip_id, customer_id, car_id, tariff_id, started_at)
