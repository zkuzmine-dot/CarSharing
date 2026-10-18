"""In-memory application service using explicit dependencies and copy-on-write."""

from __future__ import annotations

import math
import random
from datetime import datetime
from typing import Callable

from carsharing.calculations import (
    TripPricing,
    apply_promo_discount,
    calculate_trip_cost,
)
from carsharing.config import Settings
from carsharing.models import Booking, Car, Customer, Tariff, Trip, Zone
from carsharing.transitions import (
    block_debtors as calculate_debtor_updates,
    cancel_booking as calculate_booking_cancellation,
    create_booking as calculate_booking_creation,
    credit_balance,
    debit_balance,
    finish_trip as calculate_trip_finish,
    start_trip as calculate_trip_start,
    transfer_car as calculate_car_transfer,
)


class CarsharingService:
    def __init__(
        self,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings if settings is not None else Settings()
        self._clock = clock if clock is not None else lambda: datetime.now()
        self._rng = rng if rng is not None else random.Random()
        self.customers: dict[str, Customer] = {}
        self.cars: dict[str, Car] = {}
        self.zones: dict[str, Zone] = {}
        self.tariffs: dict[str, Tariff] = {}
        self.bookings: dict[str, Booking] = {}
        self.trips: dict[str, Trip] = {}
        self._booking_sequence = 0
        self._trip_sequence = 0
        self.total_finished = 0
        self._load_initial_data()

    def _load_initial_data(self) -> None:
        """Create all initial domain records in memory."""
        self.zones = {
            "center": Zone(
                "center", "Центр", (0.0, 0.0), 10.0, True, ("car-1", "car-3")
            ),
            "north": Zone("north", "Север", (0.0, 15.0), 5.0, True, ("car-2",)),
            "restricted": Zone(
                "restricted", "Закрытая зона", (15.0, 0.0), 3.0, False
            ),
        }
        self.cars = {
            "car-1": Car(
                "car-1", "Kia Rio", "A100AA", 82.0, 12000.0, "available", "center"
            ),
            "car-2": Car(
                "car-2", "Skoda Rapid", "B200BB", 64.0, 23000.0, "available", "north"
            ),
            "car-3": Car(
                "car-3",
                "Hyundai Solaris",
                "C300CC",
                45.0,
                9000.0,
                "available",
                "center",
            ),
        }
        self.tariffs = {
            "standard": Tariff.create("standard", 10.0, 8.0, 2.0, 1.5),
            "comfort": Tariff.create("comfort", 14.0, 10.0, 3.0, 1.7),
        }

    def register_customer(
        self,
        customer_id: str,
        name: str,
        license_number: str,
        balance: float = 0.0,
        license_valid: bool = True,
    ) -> Customer:
        if customer_id in self.customers:
            raise ValueError("Клиент с таким идентификатором уже существует")
        if not license_valid or not license_number.strip():
            raise ValueError("Водительское удостоверение не прошло проверку")
        if balance < 0:
            raise ValueError("Начальный баланс не может быть отрицательным")
        customer = Customer(customer_id, name, license_number, balance)
        self.customers[customer_id] = customer
        return customer

    def find_nearest_cars(
        self, point: tuple[float, float], limit: int = 3
    ) -> tuple[Car, ...]:
        if limit < 0:
            raise ValueError("Лимит не может быть отрицательным")
        available = [car for car in self.cars.values() if car.status == "available"]
        return tuple(
            sorted(
                available,
                key=lambda car: self._distance(
                    point, self._zone(car.zone_id).center
                ),
            )[:limit]
        )

    def create_booking(self, customer_id: str, car_id: str) -> Booking:
        customer = self._customer(customer_id)
        car = self._car(car_id)
        result = calculate_booking_creation(
            customer,
            car,
            f"booking-{self._booking_sequence + 1:03d}",
            self._clock(),
            self.settings,
        )
        if result.error is not None:
            raise ValueError(result.error)
        if result.booking is None:
            raise RuntimeError("Бронь отсутствует в успешном результате")
        self._booking_sequence += 1
        self.bookings[result.booking.id] = result.booking
        self.customers[customer_id] = result.customer
        self.cars[car_id] = result.car
        return result.booking

    def cancel_booking(self, booking_id: str) -> float:
        booking = self._booking(booking_id)
        customer = self._customer(booking.customer_id)
        car = self._car(booking.car_id)
        result = calculate_booking_cancellation(
            booking, customer, car, self._clock(), self.settings
        )
        if result.booking != booking:
            self.bookings[booking.id] = result.booking
        if result.car != car:
            self.cars[car.id] = result.car
        if result.customer != customer:
            self.customers[customer.id] = result.customer
        if result.error is not None:
            raise ValueError(result.error)
        return result.fee

    def start_trip(
        self,
        customer_id: str,
        car_id: str,
        tariff_id: str = "standard",
        booking_id: str | None = None,
    ) -> Trip:
        customer = self._customer(customer_id)
        car = self._car(car_id)
        tariff = self._get_or_create_tariff(tariff_id)
        booking = self._booking(booking_id) if booking_id is not None else None
        result = calculate_trip_start(
            customer,
            car,
            tariff,
            f"trip-{self._trip_sequence + 1:03d}",
            self._clock(),
            booking,
        )
        if result.booking is not None and result.booking != booking:
            self.bookings[result.booking.id] = result.booking
        if result.car != car:
            self.cars[car.id] = result.car
        if result.error is not None:
            raise ValueError(result.error)
        if result.trip is None:
            raise RuntimeError("Поездка отсутствует в успешном результате")
        self._trip_sequence += 1
        self.trips[result.trip.id] = result.trip
        self.customers[customer.id] = result.customer
        self.cars[car.id] = result.car
        self.tariffs[tariff.id] = result.tariff
        return result.trip

    def calculate_trip_cost(
        self,
        trip_id: str,
        distance: float,
        idle_minutes: int = 0,
        promo_code: str | None = None,
        use_loyalty: bool = False,
        at: datetime | None = None,
    ) -> float:
        trip = self._trip(trip_id)
        if distance < 0 or idle_minutes < 0:
            raise ValueError("Расстояние и простой не могут быть отрицательными")
        finished_at = at if at is not None else self._clock()
        customer = self._customer(trip.customer_id)
        tariff = self._tariff(trip.tariff_id)
        result = calculate_trip_cost(
            tariff,
            TripPricing(
                trip.started_at,
                finished_at,
                distance,
                idle_minutes,
                self._demand_coefficient(),
                promo_code,
                customer.loyalty_points,
                use_loyalty,
            ),
            self.settings.night_start_hour,
            self.settings.night_end_hour,
            self.settings.loyalty_discount_per_point,
            self.settings.maximum_loyalty_discount,
        )
        return result.amount

    def finish_trip(
        self,
        trip_id: str,
        distance: float,
        parking_point: tuple[float, float],
        idle_minutes: int = 0,
        promo_code: str | None = None,
        use_loyalty: bool = False,
    ) -> float:
        trip = self._trip(trip_id)
        customer = self._customer(trip.customer_id)
        car = self._car(trip.car_id)
        tariff = self._tariff(trip.tariff_id)
        destination = self._find_zone(parking_point)
        parking_allowed = destination is not None and destination.allows_parking
        result = calculate_trip_finish(
            trip,
            customer,
            car,
            tariff,
            destination,
            self._clock(),
            distance,
            idle_minutes,
            self._demand_coefficient() if parking_allowed else 1.0,
            promo_code,
            use_loyalty,
            self.settings,
        )
        if result.error is not None:
            raise ValueError(result.error)
        self.trips[trip.id] = result.trip
        self.customers[customer.id] = result.customer
        self.cars[car.id] = result.car
        self.total_finished += 1
        print(result.receipt)
        return result.amount

    def top_up_balance(self, customer_id: str, amount: float) -> float:
        customer = self._customer(customer_id)
        result = credit_balance(customer, amount)
        if result.error is not None:
            raise ValueError(result.error)
        self.customers[customer_id] = result.customer
        return result.customer.balance

    def charge_balance(self, customer_id: str, amount: float) -> float:
        customer = self._customer(customer_id)
        result = debit_balance(customer, amount)
        if result.error is not None:
            raise ValueError(result.error)
        self.customers[customer_id] = result.customer
        return result.customer.balance

    def block_debtors(self, minimum_balance: float = 0.0) -> tuple[Customer, ...]:
        if minimum_balance < 0:
            raise ValueError("Порог долга не может быть отрицательным")
        before = tuple(self.customers.values())
        after = calculate_debtor_updates(before, minimum_balance)
        blocked: list[Customer] = []
        for original, updated in zip(before, after):
            if original.balance < minimum_balance:
                blocked.append(updated)
                if updated != original:
                    self.customers[updated.id] = updated
        return tuple(blocked)

    def transfer_car(self, car_id: str, zone_id: str) -> Car:
        car = self._car(car_id)
        zone = self._zone(zone_id)
        old_zone = self._zone(car.zone_id)
        result = calculate_car_transfer(car, old_zone, zone)
        if result.error is not None:
            raise ValueError(result.error)
        self.cars[car.id] = result.car
        self.zones[old_zone.id] = result.old_zone
        self.zones[zone.id] = result.new_zone
        return result.car

    def monthly_statement(
        self, customer_id: str, year: int, month: int
    ) -> dict[str, object]:
        customer = self._customer(customer_id)
        if month < 1 or month > 12:
            raise ValueError("Месяц должен быть от 1 до 12")
        trips: list[Trip] = []
        for trip_id in customer.trips:
            trip = self.trips[trip_id]
            finished_at = trip.finished_at
            if (
                finished_at is not None
                and finished_at.year == year
                and finished_at.month == month
            ):
                trips.append(trip)
        total = round(sum(trip.cost for trip in trips), 2)
        return {
            "customer": customer.name,
            "period": f"{year:04d}-{month:02d}",
            "trip_count": len(trips),
            "total": total,
            "balance": round(customer.balance, 2),
            "trips": tuple((trip.id, trip.cost) for trip in trips),
        }

    def apply_promo_code(self, trip_id: str, amount: float, code: str) -> float:
        if amount < 0:
            raise ValueError("Сумма не может быть отрицательной")
        trip = self._trip(trip_id)
        return round(
            apply_promo_discount(
                amount,
                code,
                trip.started_at,
                self.settings.night_start_hour,
                self.settings.night_end_hour,
            ),
            2,
        )

    def _get_or_create_tariff(self, tariff_id: str) -> Tariff:
        if tariff_id == "economy" and tariff_id not in self.tariffs:
            tariff = Tariff.create("economy", 7.0, 6.0, 1.5, 1.4)
            self.tariffs[tariff.id] = tariff
            return tariff
        return self._tariff(tariff_id)

    @staticmethod
    def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
        return math.sqrt((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2)

    def _demand_coefficient(self) -> float:
        return round(
            self._rng.uniform(
                self.settings.demand_minimum, self.settings.demand_maximum
            ),
            2,
        )

    def _find_zone(self, point: tuple[float, float]) -> Zone | None:
        return next(
            (zone for zone in self.zones.values() if zone.contains(point)),
            None,
        )

    def _customer(self, customer_id: str) -> Customer:
        try:
            return self.customers[customer_id]
        except KeyError as error:
            raise ValueError(f"Клиент {customer_id!r} не найден") from error

    def _car(self, car_id: str) -> Car:
        try:
            return self.cars[car_id]
        except KeyError as error:
            raise ValueError(f"Автомобиль {car_id!r} не найден") from error

    def _booking(self, booking_id: str) -> Booking:
        try:
            return self.bookings[booking_id]
        except KeyError as error:
            raise ValueError(f"Бронь {booking_id!r} не найдена") from error

    def _trip(self, trip_id: str) -> Trip:
        try:
            return self.trips[trip_id]
        except KeyError as error:
            raise ValueError(f"Поездка {trip_id!r} не найдена") from error

    def _tariff(self, tariff_id: str) -> Tariff:
        try:
            return self.tariffs[tariff_id]
        except KeyError as error:
            raise ValueError(f"Тариф {tariff_id!r} не найден") from error

    def _zone(self, zone_id: str) -> Zone:
        try:
            return self.zones[zone_id]
        except KeyError as error:
            raise ValueError(f"Зона {zone_id!r} не найдена") from error
