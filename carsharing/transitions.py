"""Pure copy-on-write transitions for car-sharing actions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from carsharing.calculations import (
    TripPricing,
    calculate_cancellation_fee,
    calculate_fine,
    calculate_trip_cost,
)
from carsharing.config import Settings
from carsharing.models import Booking, Car, Customer, Tariff, Trip, Zone


@dataclass(frozen=True)
class BookingTransition:
    booking: Booking | None
    customer: Customer
    car: Car
    error: str | None = None


@dataclass(frozen=True)
class CancellationTransition:
    booking: Booking
    customer: Customer
    car: Car
    fee: float
    error: str | None = None


@dataclass(frozen=True)
class TripStartTransition:
    trip: Trip | None
    customer: Customer
    car: Car
    tariff: Tariff
    booking: Booking | None = None
    error: str | None = None


@dataclass(frozen=True)
class TripFinishTransition:
    trip: Trip
    customer: Customer
    car: Car
    amount: float
    receipt: str
    error: str | None = None


@dataclass(frozen=True)
class BalanceTransition:
    customer: Customer
    error: str | None = None


@dataclass(frozen=True)
class TransferTransition:
    car: Car
    old_zone: Zone
    new_zone: Zone
    error: str | None = None


def create_booking(
    customer: Customer,
    car: Car,
    booking_id: str,
    now: datetime,
    settings: Settings,
) -> BookingTransition:
    if customer.status != "active":
        return BookingTransition(None, customer, car, "Клиент заблокирован")
    if car.status != "available":
        return BookingTransition(None, customer, car, "Автомобиль недоступен")
    booking = Booking(
        booking_id,
        customer.id,
        car.id,
        now,
        now + timedelta(minutes=settings.booking_duration_minutes),
    )
    return BookingTransition(
        booking,
        replace(customer, bookings=customer.bookings + (booking.id,)),
        replace(car, status="booked"),
    )


def cancel_booking(
    booking: Booking,
    customer: Customer,
    car: Car,
    now: datetime,
    settings: Settings,
) -> CancellationTransition:
    calculation = calculate_cancellation_fee(
        booking.created_at,
        booking.expires_at,
        now,
        settings.cancellation_free_minutes,
        settings.cancellation_fee,
        booking.status == "active",
    )
    if calculation.status == "inactive":
        return CancellationTransition(
            booking, customer, car, 0.0, "Бронь уже не активна"
        )
    if calculation.status == "expired":
        return CancellationTransition(
            replace(booking, status="expired"),
            customer,
            replace(car, status="available"),
            0.0,
            "Срок брони истёк",
        )

    updated_customer = customer
    if calculation.fee > 0:
        debit = debit_balance(customer, calculation.fee)
        if debit.error is not None:
            return CancellationTransition(
                booking, customer, car, 0.0, debit.error
            )
        updated_customer = debit.customer
    return CancellationTransition(
        replace(booking, status="cancelled"),
        updated_customer,
        replace(car, status="available"),
        calculation.fee,
    )


def start_trip(
    customer: Customer,
    car: Car,
    tariff: Tariff,
    trip_id: str,
    now: datetime,
    booking: Booking | None = None,
) -> TripStartTransition:
    if customer.status != "active":
        return TripStartTransition(
            None, customer, car, tariff, error="Клиент заблокирован"
        )
    if customer.balance <= 0:
        return TripStartTransition(
            None,
            customer,
            car,
            tariff,
            error="Недостаточно средств для начала поездки",
        )
    updated_booking = booking
    if booking is not None:
        if (
            booking.customer_id != customer.id
            or booking.car_id != car.id
            or booking.status != "active"
        ):
            return TripStartTransition(
                None,
                customer,
                car,
                tariff,
                error="Бронь не соответствует клиенту или машине",
            )
        if now > booking.expires_at:
            return TripStartTransition(
                None,
                customer,
                replace(car, status="available"),
                tariff,
                replace(booking, status="expired"),
                "Срок брони истёк",
            )
        if car.status != "booked":
            return TripStartTransition(
                None,
                customer,
                car,
                tariff,
                error="Автомобиль по брони недоступен",
            )
        updated_booking = replace(booking, status="used")
    elif car.status != "available":
        return TripStartTransition(
            None, customer, car, tariff, error="Автомобиль недоступен"
        )

    trip = Trip.start(trip_id, customer.id, car.id, tariff.id, now)
    return TripStartTransition(
        trip,
        replace(customer, trips=customer.trips + (trip.id,)),
        replace(car, status="in_use", trips=car.trips + (trip.id,)),
        replace(tariff, trips=tariff.trips + (trip.id,)),
        updated_booking,
    )


def finish_trip(
    trip: Trip,
    customer: Customer,
    car: Car,
    tariff: Tariff,
    destination: Zone | None,
    finished_at: datetime,
    distance: float,
    idle_minutes: int,
    demand_coefficient: float,
    promo_code: str | None,
    use_loyalty: bool,
    settings: Settings,
) -> TripFinishTransition:
    parking_allowed = destination is not None and destination.allows_parking
    fine = calculate_fine(parking_allowed, settings.parking_fine)
    if not parking_allowed:
        customer_result = debit_balance(customer, fine)
        if customer_result.error is not None:
            return TripFinishTransition(
                trip, customer, car, 0.0, "", customer_result.error
            )
        updated_trip = replace(
            trip,
            finished_at=finished_at,
            distance=distance,
            cost=fine,
            status="finished",
        )
        updated_car = replace(
            car,
            status="available",
            zone_id=destination.id if destination is not None else car.zone_id,
        )
        return TripFinishTransition(
            updated_trip,
            customer_result.customer,
            updated_car,
            fine,
            f"Штраф за парковку {trip.id}: {fine:.2f} руб.",
        )

    if trip.status != "active":
        return TripFinishTransition(
            trip, customer, car, 0.0, "", "Поездка уже завершена"
        )
    if distance < 0 or idle_minutes < 0:
        return TripFinishTransition(
            trip,
            customer,
            car,
            0.0,
            "",
            "Расстояние и простой не могут быть отрицательными",
        )
    if destination is None:
        return TripFinishTransition(
            trip, customer, car, 0.0, "", "Зона парковки не определена"
        )

    calculation = calculate_trip_cost(
        tariff,
        TripPricing(
            trip.started_at,
            finished_at,
            distance,
            idle_minutes,
            demand_coefficient,
            promo_code,
            customer.loyalty_points,
            use_loyalty,
        ),
        settings.night_start_hour,
        settings.night_end_hour,
        settings.loyalty_discount_per_point,
        settings.maximum_loyalty_discount,
    )
    updated_customer = replace(
        customer,
        loyalty_points=(
            calculation.remaining_points + calculation.earned_points
        ),
    )
    customer_result = debit_balance(updated_customer, calculation.amount)
    if customer_result.error is not None:
        return TripFinishTransition(
            trip, customer, car, 0.0, "", customer_result.error
        )
    updated_trip = replace(
        trip,
        finished_at=finished_at,
        distance=distance,
        cost=calculation.amount,
        status="finished",
    )
    updated_car = replace(
        car,
        status="available",
        zone_id=destination.id,
        mileage=car.mileage + distance,
        fuel_level=max(0.0, car.fuel_level - distance * 0.04),
    )
    return TripFinishTransition(
        updated_trip,
        customer_result.customer,
        updated_car,
        calculation.amount,
        f"Чек {trip.id}: {calculation.amount:.2f} руб.",
    )


def debit_balance(customer: Customer, amount: float) -> BalanceTransition:
    if amount <= 0:
        return BalanceTransition(customer, "Списание должно быть положительным")
    balance = customer.balance - amount
    return BalanceTransition(
        replace(
            customer,
            balance=balance,
            status="blocked" if balance < 0 else customer.status,
        )
    )


def credit_balance(customer: Customer, amount: float) -> BalanceTransition:
    if amount <= 0:
        return BalanceTransition(customer, "Пополнение должно быть положительным")
    balance = customer.balance + amount
    status = (
        "active"
        if customer.status == "blocked" and balance >= 0
        else customer.status
    )
    return BalanceTransition(replace(customer, balance=balance, status=status))


def block_debtors(
    customers: tuple[Customer, ...], minimum_balance: float
) -> tuple[Customer, ...]:
    return tuple(
        replace(customer, status="blocked")
        if customer.balance < minimum_balance
        else customer
        for customer in customers
    )


def transfer_car(car: Car, old_zone: Zone, new_zone: Zone) -> TransferTransition:
    if car.status != "available":
        return TransferTransition(
            car,
            old_zone,
            new_zone,
            "Перегонять можно только свободный автомобиль",
        )
    return TransferTransition(
        replace(car, zone_id=new_zone.id),
        replace(old_zone, cars=tuple(item for item in old_zone.cars if item != car.id)),
        replace(new_zone, cars=new_zone.cars + (car.id,)),
    )
