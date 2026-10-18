"""Characterization tests for the deliberately imperative initial version."""

from __future__ import annotations

import random
from datetime import datetime

import pytest
from freezegun import freeze_time

from carsharing.models import Trip
from carsharing.service import CarsharingService


@pytest.fixture
def service() -> CarsharingService:
    return CarsharingService()


@pytest.fixture
def customer(service: CarsharingService):
    return service.register_customer("c1", "Анна", "A123456", 5000)


def test_register_customer_and_validate_license(service: CarsharingService) -> None:
    customer = service.register_customer("c1", "Анна", "A123456", 250)
    assert (customer.name, customer.balance, customer.status) == (
        "Анна",
        250,
        "active",
    )
    with pytest.raises(ValueError, match="удостоверение"):
        service.register_customer("c2", "Борис", "", license_valid=False)


def test_find_nearest_available_cars(service: CarsharingService) -> None:
    cars = service.find_nearest_cars((0.0, 0.0), limit=2)
    assert [car.id for car in cars] == ["car-1", "car-3"]
    cars[0].status = "in_use"
    assert [car.id for car in service.find_nearest_cars((0.0, 0.0))] == [
        "car-3",
        "car-2",
    ]


def test_booking_lasts_fifteen_minutes(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    assert (booking.expires_at - booking.created_at).total_seconds() == 900
    assert booking.car.status == "booked"


def test_cancel_booking_is_free_for_first_five_minutes(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:05:00"):
        fee = service.cancel_booking(booking.id)
    assert fee == 0
    assert booking.status == "cancelled"
    assert booking.car.status == "available"
    assert customer.balance == 5000


def test_late_booking_cancellation_charges_fee(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:06:00"):
        fee = service.cancel_booking(booking.id)
    assert fee == 120
    assert customer.balance == 4880


def test_start_trip_from_booking(service: CarsharingService, customer) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:02:00"):
        trip = service.start_trip(customer.id, "car-1", booking_id=booking.id)
    assert trip.status == "active"
    assert booking.status == "used"
    assert trip.car.status == "in_use"
    assert customer.trips == [trip]
    assert trip in trip.car.trips
    assert trip in trip.tariff.trips


def test_start_trip_rejects_blocked_customer(service: CarsharingService) -> None:
    customer = service.register_customer("c1", "Анна", "A123456", 100)
    customer.status = "blocked"
    with pytest.raises(ValueError, match="заблокирован"):
        service.start_trip(customer.id, "car-1")


def test_calculate_day_cost_with_distance_and_idle(
    service: CarsharingService, customer
) -> None:
    random.seed(1)
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    random.seed(1)
    quote = service.calculate_trip_cost(
        trip.id, 3.0, idle_minutes=5, at=datetime(2025, 5, 12, 12, 10)
    )
    assert quote == 127.3


def test_calculate_night_cost_applies_night_coefficient(
    service: CarsharingService, customer
) -> None:
    random.seed(1)
    with freeze_time("2025-05-12 23:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    random.seed(1)
    quote = service.calculate_trip_cost(
        trip.id, 3.0, idle_minutes=5, at=datetime(2025, 5, 12, 23, 10)
    )
    assert quote == 190.95


def test_promo_code_and_loyalty_discount(service: CarsharingService, customer) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    customer.loyalty_points = 100
    assert service.apply_promo_code(trip.id, 200, "WELCOME10") == 180
    assert service.apply_promo_code(trip.id, 200, "UNKNOWN") == 200
    random.seed(1)
    assert service.calculate_trip_cost(
        trip.id,
        1,
        at=datetime(2025, 5, 12, 12, 1),
        promo_code="WELCOME10",
        use_loyalty=True,
    ) == 0


def test_finish_trip_charges_updates_car_and_prints_receipt(
    service: CarsharingService, customer, capsys
) -> None:
    random.seed(1)
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    old_balance = customer.balance
    old_mileage = trip.car.mileage
    before_count = Trip.total_finished
    random.seed(1)
    with freeze_time("2025-05-12 12:10:00"):
        cost = service.finish_trip(trip.id, 3.0, (1.0, 1.0))
    assert cost == 117.8
    assert customer.balance == old_balance - cost
    assert trip.car.mileage == old_mileage + 3
    assert trip.car.status == "available"
    assert trip.status == "finished"
    assert Trip.total_finished == before_count + 1
    assert capsys.readouterr().out == f"Чек {trip.id}: 117.80 руб.\n"


def test_finish_outside_parking_zone_charges_fine(
    service: CarsharingService, customer, capsys
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    with freeze_time("2025-05-12 12:10:00"):
        amount = service.finish_trip(trip.id, 2, (15.0, 0.0))
    assert amount == 1500
    assert customer.balance == 3500
    assert trip.status == "finished"
    assert "Штраф за парковку" in capsys.readouterr().out


def test_top_up_and_charge_balance(service: CarsharingService, customer) -> None:
    assert service.top_up_balance(customer.id, 200) == 5200
    assert service.charge_balance(customer.id, 50) == 5150
    with pytest.raises(ValueError, match="положительным"):
        service.top_up_balance(customer.id, 0)


def test_negative_balance_blocks_customer_and_top_up_unblocks(
    service: CarsharingService,
) -> None:
    customer = service.register_customer("c1", "Анна", "A123456", 10)
    assert service.charge_balance(customer.id, 20) == -10
    assert customer.status == "blocked"
    assert service.block_debtors() == [customer]
    service.top_up_balance(customer.id, 20)
    assert (customer.status, customer.balance) == ("active", 10)


def test_transfer_car_between_zones(service: CarsharingService) -> None:
    original_zone = service.zones["north"]
    car = service.transfer_car("car-2", "center")
    assert car.zone.name == "Центр"
    assert car.status == "available"
    assert car not in original_zone.cars
    assert car in service.zones["center"].cars
    car.status = "in_use"
    with pytest.raises(ValueError, match="свободный"):
        service.transfer_car("car-2", "north")


def test_economy_tariff_is_built_when_first_requested(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1", tariff_id="economy")
    assert trip.tariff.per_minute == 7
    assert service.tariffs["economy"] is trip.tariff


def test_monthly_statement_includes_only_finished_trips_in_period(
    service: CarsharingService, customer
) -> None:
    random.seed(1)
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    random.seed(1)
    with freeze_time("2025-05-12 12:10:00"):
        service.finish_trip(trip.id, 1, (1.0, 1.0))
    statement = service.monthly_statement(customer.id, 2025, 5)
    assert statement["trip_count"] == 1
    assert statement["total"] == trip.cost
    assert statement["period"] == "2025-05"
    assert service.monthly_statement(customer.id, 2025, 4)["trip_count"] == 0


def test_customer_trips_property_exposes_mutable_list(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    customer.trips.clear()
    assert service.monthly_statement(customer.id, 2025, 5)["trip_count"] == 0
    assert trip not in customer.trips


def test_booking_cancel_after_expiry_raises_and_frees_car(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:16:00"):
        with pytest.raises(ValueError, match="истёк"):
            service.cancel_booking(booking.id)
    assert booking.status == "expired"
    assert booking.car.status == "available"


def test_demand_coefficient_is_seeded_for_characterization(
    service: CarsharingService, customer
) -> None:
    random.seed(7)
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    random.seed(7)
    quote = service.calculate_trip_cost(
        trip.id, 1, at=datetime(2025, 5, 12, 12, 1)
    )
    assert quote == 18.54
