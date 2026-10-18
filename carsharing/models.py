"""Mutable object-per-entity domain model used by the initial application."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar


class Customer:
    def __init__(
        self,
        customer_id: str,
        name: str,
        license_number: str,
        balance: float = 0.0,
        status: str = "active",
        loyalty_points: int = 0,
    ) -> None:
        self.id = customer_id
        self.name = name
        self.license_number = license_number
        self.balance = balance
        self.status = status
        self.loyalty_points = loyalty_points
        self._trips: list[Trip] = []
        self.bookings: list[Booking] = []

    @property
    def trips(self) -> list[Trip]:
        """Returns the internal list directly; callers can mutate customer history."""
        return self._trips

    def add_trip(self, trip: Trip) -> None:
        self._trips.append(trip)

    def credit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Пополнение должно быть положительным")
        self.balance += amount

    def debit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Списание должно быть положительным")
        self.balance -= amount
        if self.balance < 0:
            self.status = "blocked"

    def __repr__(self) -> str:
        return f"Customer({self.id!r}, {self.name!r}, balance={self.balance:.2f})"


class Zone:
    def __init__(
        self,
        zone_id: str,
        name: str,
        center: tuple[float, float],
        radius: float,
        allows_parking: bool = True,
    ) -> None:
        self.id = zone_id
        self.name = name
        self.center = center
        self.radius = radius
        self.allows_parking = allows_parking
        self.cars: list[Car] = []

    def contains(self, point: tuple[float, float]) -> bool:
        dx = point[0] - self.center[0]
        dy = point[1] - self.center[1]
        return dx * dx + dy * dy <= self.radius * self.radius

    def __repr__(self) -> str:
        return f"Zone({self.id!r}, {self.name!r})"


class Car:
    def __init__(
        self,
        car_id: str,
        model: str,
        plate: str,
        fuel_level: float,
        mileage: float,
        status: str,
        zone: Zone,
    ) -> None:
        self.id = car_id
        self.model = model
        self.plate = plate
        self.fuel_level = fuel_level
        self.mileage = mileage
        self.status = status
        self.zone = zone
        self.trips: list[Trip] = []
        zone.cars.append(self)

    def __repr__(self) -> str:
        return f"Car({self.id!r}, {self.model!r}, status={self.status!r})"


class Tariff:
    def __init__(
        self,
        tariff_id: str,
        per_minute: float,
        per_km: float,
        parking_per_minute: float,
        night_coefficient: float,
    ) -> None:
        self.id = tariff_id
        self.per_minute = per_minute
        self.per_km = per_km
        self.parking_per_minute = parking_per_minute
        self.night_coefficient = night_coefficient
        self.trips: list[Trip] = []

    def calc_day_cost(self, minutes: int, distance: float, idle_minutes: int) -> float:
        """Day fare. Nearly duplicated by calc_night_cost on purpose."""
        driving = minutes * self.per_minute + distance * self.per_km
        parking = idle_minutes * self.parking_per_minute
        return round(driving + parking, 2)

    def calc_night_cost(
        self, minutes: int, distance: float, idle_minutes: int
    ) -> float:
        """Night fare. Nearly duplicated by calc_day_cost on purpose."""
        driving = minutes * self.per_minute * self.night_coefficient
        driving += distance * self.per_km * self.night_coefficient
        parking = idle_minutes * self.parking_per_minute * self.night_coefficient
        return round(driving + parking, 2)

    def __repr__(self) -> str:
        return f"Tariff({self.id!r})"


class Booking:
    def __init__(
        self,
        booking_id: str,
        customer: Customer,
        car: Car,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.id = booking_id
        self.customer = customer
        self.car = car
        self.created_at = created_at
        self.expires_at = expires_at
        self.status = "active"

    def cancel(self) -> float:
        """Cancels now, returning fee; raises when this booking cannot be cancelled."""
        if self.status != "active":
            raise ValueError("Бронь уже не активна")
        now = datetime.now()
        if now > self.expires_at:
            self.status = "expired"
            self.car.status = "available"
            raise ValueError("Срок брони истёк")

        elapsed_minutes = (now - self.created_at).total_seconds() / 60
        from carsharing.config import config

        fee = 0.0
        if elapsed_minutes > config.cancellation_free_minutes:
            fee = config.cancellation_fee
            self.customer.debit(fee)
        self.status = "cancelled"
        self.car.status = "available"
        return fee

    def __repr__(self) -> str:
        return f"Booking({self.id!r}, status={self.status!r})"


class Trip:
    total_finished: ClassVar[int] = 0

    def __init__(
        self,
        trip_id: str,
        customer: Customer,
        car: Car,
        tariff: Tariff,
        started_at: datetime,
        finished_at: datetime | None = None,
        distance: float = 0.0,
        cost: float = 0.0,
    ) -> None:
        self.id = trip_id
        self.customer = customer
        self.car = car
        self.tariff = tariff
        self.started_at = started_at
        self.finished_at = finished_at
        self.distance = distance
        self.cost = cost
        self.status = "active"

    def finish(
        self,
        distance: float,
        idle_minutes: int,
        promo_code: str | None = None,
        use_loyalty: bool = False,
    ) -> float:
        """Performs calculations and all finish side effects in one method."""
        if self.status != "active":
            raise ValueError("Поездка уже завершена")
        if distance < 0 or idle_minutes < 0:
            raise ValueError("Расстояние и простой не могут быть отрицательными")

        finished_at = datetime.now()
        minutes = max(1, int((finished_at - self.started_at).total_seconds() // 60))
        night = finished_at.hour >= 23 or finished_at.hour < 6
        if night:
            cost = self.tariff.calc_night_cost(minutes, distance, idle_minutes)
        else:
            cost = self.tariff.calc_day_cost(minutes, distance, idle_minutes)
        cost *= self._demand_coefficient()
        cost = self._apply_discounts(cost, promo_code, use_loyalty)
        cost = round(cost, 2)

        self.distance = distance
        self.finished_at = finished_at
        self.cost = cost
        self.status = "finished"
        self.customer.debit(cost)
        self.car.mileage += distance
        self.car.fuel_level = max(0.0, self.car.fuel_level - distance * 0.04)
        self.car.status = "available"
        Trip.total_finished += 1
        print(f"Чек {self.id}: {self.cost:.2f} руб.")
        return cost

    def _demand_coefficient(self) -> float:
        import random

        from carsharing.config import config

        return round(
            random.uniform(config.demand_minimum, config.demand_maximum), 2
        )

    def _apply_discounts(
        self, cost: float, promo_code: str | None, use_loyalty: bool
    ) -> float:
        if promo_code == "WELCOME10":
            cost *= 0.9
        elif promo_code == "NIGHT20" and (
            self.started_at.hour >= 23 or self.started_at.hour < 6
        ):
            cost *= 0.8
        if use_loyalty:
            from carsharing.config import config

            discount = min(
                self.customer.loyalty_points
                * config.loyalty_discount_per_point,
                config.maximum_loyalty_discount,
                cost,
            )
            cost -= discount
            self.customer.loyalty_points -= int(discount)
        self.customer.loyalty_points += int(cost // 100)
        return cost

    def __repr__(self) -> str:
        return f"Trip({self.id!r}, status={self.status!r}, cost={self.cost:.2f})"
