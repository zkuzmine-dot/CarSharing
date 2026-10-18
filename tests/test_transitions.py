from datetime import datetime, timedelta

from carsharing.config import Settings
from carsharing.models import Car, Customer, Tariff, Zone
from carsharing.transitions import create_booking, finish_trip, start_trip


def test_booking_transition_returns_copies_without_mutating_inputs() -> None:
    customer = Customer("customer-1", "Анна", "A123456", balance=100)
    car = Car("car-1", "Kia Rio", "A100AA", 80, 1000, "available", "center")
    now = datetime(2025, 5, 12, 12)

    result = create_booking(customer, car, "booking-1", now, Settings())

    assert result.error is None
    assert result.booking is not None
    assert result.booking.expires_at == now + timedelta(minutes=15)
    assert result.customer.bookings == ("booking-1",)
    assert result.car.status == "booked"
    assert customer.bookings == ()
    assert car.status == "available"


def test_trip_transitions_return_new_entities_and_preserve_originals() -> None:
    customer = Customer("customer-1", "Анна", "A123456", balance=1000)
    car = Car("car-1", "Kia Rio", "A100AA", 80, 1000, "available", "center")
    tariff = Tariff.create("standard", 10, 8, 2, 1.5)
    zone = Zone("center", "Центр", (0, 0), 10)
    started_at = datetime(2025, 5, 12, 12)
    start = start_trip(customer, car, tariff, "trip-1", started_at)

    assert start.error is None
    assert start.trip is not None
    finish = finish_trip(
        start.trip,
        start.customer,
        start.car,
        start.tariff,
        zone,
        datetime(2025, 5, 12, 12, 10),
        1,
        0,
        1,
        None,
        False,
        Settings(),
    )

    assert finish.error is None
    assert finish.trip.status == "finished"
    assert finish.customer.balance == customer.balance - finish.amount
    assert finish.car.status == "available"
    assert customer.trips == ()
    assert car.status == "available"
    assert tariff.trips == ()
