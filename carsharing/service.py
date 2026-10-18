"""Imperative application service and in-memory initial data."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from carsharing.calculations import (
    apply_promo_discount,
    calculate_fine,
    calculate_trip_cost,
)
from carsharing.config import config
from carsharing.models import Booking, Car, Customer, Tariff, Trip, Zone


class CarsharingService:
    def __init__(self) -> None:
        self.customers: dict[str, Customer] = {}
        self.cars: dict[str, Car] = {}
        self.zones: dict[str, Zone] = {}
        self.tariffs: dict[str, Tariff] = {}
        self.bookings: dict[str, Booking] = {}
        self.trips: dict[str, Trip] = {}
        self._booking_sequence = 0
        self._trip_sequence = 0
        self._load_initial_data()

    def _load_initial_data(self) -> None:
        """Creates all domain objects in memory on each application startup."""
        center = Zone("center", "Центр", (0.0, 0.0), 10.0, True)
        north = Zone("north", "Север", (0.0, 15.0), 5.0, True)
        restricted = Zone("restricted", "Закрытая зона", (15.0, 0.0), 3.0, False)
        self.zones = {zone.id: zone for zone in (center, north, restricted)}

        self.cars = {
            "car-1": Car(
                "car-1", "Kia Rio", "A100AA", 82.0, 12000.0, "available", center
            ),
            "car-2": Car(
                "car-2", "Skoda Rapid", "B200BB", 64.0, 23000.0, "available", north
            ),
            "car-3": Car(
                "car-3", "Hyundai Solaris", "C300CC", 45.0, 9000.0, "available", center
            ),
        }
        self.tariffs = {
            "standard": Tariff("standard", 10.0, 8.0, 2.0, 1.5),
            "comfort": Tariff("comfort", 14.0, 10.0, 3.0, 1.7),
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
    ) -> list[Car]:
        if limit < 0:
            raise ValueError("Лимит не может быть отрицательным")
        available = [car for car in self.cars.values() if car.status == "available"]
        return sorted(
            available,
            key=lambda car: self._distance(point, car.zone.center),
        )[:limit]

    def create_booking(self, customer_id: str, car_id: str) -> Booking:
        customer = self._customer(customer_id)
        car = self._car(car_id)
        self._ensure_active_customer(customer)
        if car.status != "available":
            raise ValueError("Автомобиль недоступен")

        now = datetime.now()
        booking = Booking(
            self._next_booking_id(),
            customer,
            car,
            now,
            now + timedelta(minutes=config.booking_duration_minutes),
        )
        self.bookings[booking.id] = booking
        customer.bookings.append(booking)
        car.status = "booked"
        return booking

    def cancel_booking(self, booking_id: str) -> float:
        booking = self._booking(booking_id)
        return booking.cancel(
            datetime.now(),
            config.cancellation_free_minutes,
            config.cancellation_fee,
        )

    def start_trip(
        self,
        customer_id: str,
        car_id: str,
        tariff_id: str = "standard",
        booking_id: str | None = None,
    ) -> Trip:
        customer = self._customer(customer_id)
        car = self._car(car_id)
        if tariff_id == "economy" and tariff_id not in self.tariffs:
            tariff = Tariff("economy", 7.0, 6.0, 1.5, 1.4)
            self.tariffs[tariff.id] = tariff
        else:
            tariff = self._tariff(tariff_id)
        self._ensure_active_customer(customer)
        if customer.balance <= 0:
            raise ValueError("Недостаточно средств для начала поездки")

        if booking_id is not None:
            booking = self._booking(booking_id)
            if (
                booking.customer is not customer
                or booking.car is not car
                or booking.status != "active"
            ):
                raise ValueError("Бронь не соответствует клиенту или машине")
            if datetime.now() > booking.expires_at:
                booking.status = "expired"
                car.status = "available"
                raise ValueError("Срок брони истёк")
            booking.status = "used"
        elif car.status != "available":
            raise ValueError("Автомобиль недоступен")

        if booking_id is not None and car.status != "booked":
            raise ValueError("Автомобиль по брони недоступен")
        car.status = "in_use"
        trip = Trip(
            self._next_trip_id(),
            customer,
            car,
            tariff,
            datetime.now(),
        )
        self.trips[trip.id] = trip
        customer.add_trip(trip)
        car.trips.append(trip)
        tariff.trips.append(trip)
        return trip

    def calculate_trip_cost(
        self,
        trip_id: str,
        distance: float,
        idle_minutes: int = 0,
        promo_code: str | None = None,
        use_loyalty: bool = False,
        at: datetime | None = None,
    ) -> float:
        """Returns a quote; it shares pricing rules with the mutating finish path."""
        trip = self._trip(trip_id)
        if distance < 0 or idle_minutes < 0:
            raise ValueError("Расстояние и простой не могут быть отрицательными")
        now = at or datetime.now()
        result = calculate_trip_cost(
            trip.tariff,
            trip.started_at,
            now,
            distance,
            idle_minutes,
            self._demand_coefficient(),
            promo_code,
            trip.customer.loyalty_points,
            use_loyalty,
            config.night_start_hour,
            config.night_end_hour,
            config.loyalty_discount_per_point,
            config.maximum_loyalty_discount,
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
        destination = self._find_zone(parking_point)
        fine = calculate_fine(
            destination is not None and destination.allows_parking,
            config.parking_fine,
        )
        if fine > 0:
            trip.customer.debit(fine)
            trip.car.status = "available"
            if destination is not None:
                trip.car.zone = destination
            trip.status = "finished"
            trip.finished_at = datetime.now()
            trip.distance = distance
            trip.cost = fine
            print(f"Штраф за парковку {trip.id}: {fine:.2f} руб.")
            return trip.cost

        cost = trip.finish(
            distance,
            idle_minutes,
            datetime.now(),
            self._demand_coefficient(),
            config.night_start_hour,
            config.night_end_hour,
            config.loyalty_discount_per_point,
            config.maximum_loyalty_discount,
            promo_code,
            use_loyalty,
        )
        if destination is not None:
            trip.car.zone = destination
        return cost

    def top_up_balance(self, customer_id: str, amount: float) -> float:
        customer = self._customer(customer_id)
        customer.credit(amount)
        if customer.status == "blocked" and customer.balance >= 0:
            customer.status = "active"
        return customer.balance

    def charge_balance(self, customer_id: str, amount: float) -> float:
        customer = self._customer(customer_id)
        customer.debit(amount)
        return customer.balance

    def block_debtors(self, minimum_balance: float = 0.0) -> list[Customer]:
        if minimum_balance < 0:
            raise ValueError("Порог долга не может быть отрицательным")
        blocked: list[Customer] = []
        for customer in self.customers.values():
            if customer.balance < minimum_balance:
                customer.status = "blocked"
                blocked.append(customer)
        return blocked

    def transfer_car(self, car_id: str, zone_id: str) -> Car:
        car = self._car(car_id)
        zone = self._zone(zone_id)
        if car.status != "available":
            raise ValueError("Перегонять можно только свободный автомобиль")
        car.zone.cars.remove(car)
        car.zone = zone
        zone.cars.append(car)
        return car

    def monthly_statement(
        self, customer_id: str, year: int, month: int
    ) -> dict[str, object]:
        customer = self._customer(customer_id)
        if month < 1 or month > 12:
            raise ValueError("Месяц должен быть от 1 до 12")
        trips = [
            trip
            for trip in customer.trips
            if trip.finished_at is not None
            and trip.finished_at.year == year
            and trip.finished_at.month == month
        ]
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
        """Legacy helper for quoting discounts outside trip completion."""
        if amount < 0:
            raise ValueError("Сумма не может быть отрицательной")
        trip = self._trip(trip_id)
        return round(
            apply_promo_discount(
                amount,
                code,
                trip.started_at,
                config.night_start_hour,
                config.night_end_hour,
            ),
            2,
        )

    @staticmethod
    def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
        return math.sqrt((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2)

    def _demand_coefficient(self) -> float:
        return round(
            random.uniform(config.demand_minimum, config.demand_maximum), 2
        )

    def _find_zone(self, point: tuple[float, float]) -> Zone | None:
        for zone in self.zones.values():
            if zone.contains(point):
                return zone
        return None

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

    @staticmethod
    def _ensure_active_customer(customer: Customer) -> None:
        if customer.status != "active":
            raise ValueError("Клиент заблокирован")

    def _next_booking_id(self) -> str:
        self._booking_sequence += 1
        return f"booking-{self._booking_sequence:03d}"

    def _next_trip_id(self) -> str:
        self._trip_sequence += 1
        return f"trip-{self._trip_sequence:03d}"
