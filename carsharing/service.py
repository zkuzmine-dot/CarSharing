"""In-memory application service using explicit dependencies and copy-on-write."""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable

from carsharing.calculations import (
    FarePeriod,
    TripPricing,
    TripCostCalculation,
    apply_promo_discount,
    calculate_cancellation_fee,
    calculate_cost,
    calculate_fine,
    calculate_trip_cost,
)
from carsharing.config import Settings
from carsharing.models import Booking, Car, Customer, Tariff, Trip, Zone


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
        if customer.status != "active":
            raise ValueError("Клиент заблокирован")
        if car.status != "available":
            raise ValueError("Автомобиль недоступен")

        now = self._clock()
        booking = Booking(
            self._next_booking_id(),
            customer_id,
            car_id,
            now,
            now + timedelta(minutes=self.settings.booking_duration_minutes),
        )
        self.bookings[booking.id] = booking
        self.customers[customer_id] = replace(
            customer, bookings=customer.bookings + (booking.id,)
        )
        self.cars[car_id] = replace(car, status="booked")
        return booking

    def cancel_booking(self, booking_id: str) -> float:
        booking = self._booking(booking_id)
        if booking.status != "active":
            raise ValueError("Бронь уже не активна")
        now = self._clock()
        result = calculate_cancellation_fee(
            booking.created_at,
            booking.expires_at,
            now,
            self.settings.cancellation_free_minutes,
            self.settings.cancellation_fee,
            True,
        )
        car = self._car(booking.car_id)
        if result.status == "expired":
            self.bookings[booking.id] = replace(booking, status="expired")
            self.cars[car.id] = replace(car, status="available")
            raise ValueError("Срок брони истёк")

        customer = self._customer(booking.customer_id)
        self.bookings[booking.id] = replace(booking, status="cancelled")
        self.cars[car.id] = replace(car, status="available")
        if result.fee > 0:
            self.customers[customer.id] = self._debit_customer(customer, result.fee)
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
        self._ensure_active_customer(customer)
        if customer.balance <= 0:
            raise ValueError("Недостаточно средств для начала поездки")

        booking: Booking | None = None
        if booking_id is not None:
            booking = self._booking(booking_id)
            if (
                booking.customer_id != customer.id
                or booking.car_id != car.id
                or booking.status != "active"
            ):
                raise ValueError("Бронь не соответствует клиенту или машине")
            if self._clock() > booking.expires_at:
                self.bookings[booking.id] = replace(booking, status="expired")
                self.cars[car.id] = replace(car, status="available")
                raise ValueError("Срок брони истёк")
            if car.status != "booked":
                raise ValueError("Автомобиль по брони недоступен")
            self.bookings[booking.id] = replace(booking, status="used")
        elif car.status != "available":
            raise ValueError("Автомобиль недоступен")

        trip = Trip.start(
            self._next_trip_id(),
            customer.id,
            car.id,
            tariff.id,
            self._clock(),
        )
        self.trips[trip.id] = trip
        self.customers[customer.id] = replace(
            customer, trips=customer.trips + (trip.id,)
        )
        self.cars[car.id] = replace(
            car, status="in_use", trips=car.trips + (trip.id,)
        )
        self.tariffs[tariff.id] = replace(
            tariff, trips=tariff.trips + (trip.id,)
        )
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
        destination = self._find_zone(parking_point)
        parking_allowed = destination is not None and destination.allows_parking
        fine = calculate_fine(parking_allowed, self.settings.parking_fine)
        customer = self._customer(trip.customer_id)
        car = self._car(trip.car_id)

        if not parking_allowed:
            self.customers[customer.id] = self._debit_customer(customer, fine)
            self.cars[car.id] = replace(
                car,
                status="available",
                zone_id=destination.id if destination is not None else car.zone_id,
            )
            self.trips[trip.id] = replace(
                trip,
                finished_at=self._clock(),
                distance=distance,
                cost=fine,
                status="finished",
            )
            self.total_finished += 1
            print(f"Штраф за парковку {trip.id}: {fine:.2f} руб.")
            return fine

        if destination is None:
            raise RuntimeError("Разрешённая парковка должна принадлежать зоне")
        if distance < 0 or idle_minutes < 0:
            raise ValueError("Расстояние и простой не могут быть отрицательными")
        tariff = self._tariff(trip.tariff_id)
        finished_at = self._clock()
        calculation = self._calculate_trip_cost(
            trip,
            customer,
            tariff,
            finished_at,
            distance,
            idle_minutes,
            promo_code,
            use_loyalty,
        )
        updated_customer = replace(
            customer,
            loyalty_points=calculation.remaining_points + calculation.earned_points,
        )
        updated_customer = self._debit_customer(updated_customer, calculation.amount)
        self.customers[customer.id] = updated_customer
        self.trips[trip.id] = replace(
            trip,
            finished_at=finished_at,
            distance=distance,
            cost=calculation.amount,
            status="finished",
        )
        self.cars[car.id] = replace(
            car,
            status="available",
            zone_id=destination.id,
            mileage=car.mileage + distance,
            fuel_level=max(0.0, car.fuel_level - distance * 0.04),
        )
        self.total_finished += 1
        print(f"Чек {trip.id}: {calculation.amount:.2f} руб.")
        return calculation.amount

    def top_up_balance(self, customer_id: str, amount: float) -> float:
        if amount <= 0:
            raise ValueError("Пополнение должно быть положительным")
        customer = self._customer(customer_id)
        balance = customer.balance + amount
        status = (
            "active"
            if customer.status == "blocked" and balance >= 0
            else customer.status
        )
        self.customers[customer_id] = replace(
            customer, balance=balance, status=status
        )
        return balance

    def charge_balance(self, customer_id: str, amount: float) -> float:
        customer = self._customer(customer_id)
        updated = self._debit_customer(customer, amount)
        self.customers[customer_id] = updated
        return updated.balance

    def block_debtors(self, minimum_balance: float = 0.0) -> tuple[Customer, ...]:
        if minimum_balance < 0:
            raise ValueError("Порог долга не может быть отрицательным")
        blocked: list[Customer] = []
        for customer in tuple(self.customers.values()):
            if customer.balance < minimum_balance:
                updated = replace(customer, status="blocked")
                self.customers[customer.id] = updated
                blocked.append(updated)
        return tuple(blocked)

    def transfer_car(self, car_id: str, zone_id: str) -> Car:
        car = self._car(car_id)
        zone = self._zone(zone_id)
        if car.status != "available":
            raise ValueError("Перегонять можно только свободный автомобиль")
        old_zone = self._zone(car.zone_id)
        self.zones[old_zone.id] = replace(
            old_zone, cars=tuple(item for item in old_zone.cars if item != car.id)
        )
        self.zones[zone.id] = replace(zone, cars=zone.cars + (car.id,))
        transferred = replace(car, zone_id=zone.id)
        self.cars[car.id] = transferred
        return transferred

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

    def _calculate_trip_cost(
        self,
        trip: Trip,
        customer: Customer,
        tariff: Tariff,
        finished_at: datetime,
        distance: float,
        idle_minutes: int,
        promo_code: str | None,
        use_loyalty: bool,
    ) -> TripCostCalculation:
        return calculate_trip_cost(
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

    def _debit_customer(self, customer: Customer, amount: float) -> Customer:
        if amount <= 0:
            raise ValueError("Списание должно быть положительным")
        balance = customer.balance - amount
        return replace(
            customer,
            balance=balance,
            status="blocked" if balance < 0 else customer.status,
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
