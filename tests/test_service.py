"""Characterization tests for the deliberately imperative initial version."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pytest
from freezegun import freeze_time

from carsharing.calculations import (
    FarePeriod,
    apply_loyalty_discount,
    apply_promo_discount,
    calculate_cancellation_fee,
    calculate_cost,
    calculate_fine,
    calculate_trip_cost as calculate_trip_cost_pure,
    TripPricing,
)
from carsharing.config import Settings
from carsharing.models import Customer, Tariff
from carsharing.service import CarsharingService


@pytest.fixture
def service() -> CarsharingService:
    return CarsharingService(rng=random.Random(1))


@pytest.fixture
def customer(service: CarsharingService) -> Customer:
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
    with pytest.raises(FrozenInstanceError):
        cars[0].status = "in_use"
    assert [car.id for car in service.find_nearest_cars((0.0, 0.0))] == [
        "car-1",
        "car-3",
        "car-2",
    ]


def test_booking_lasts_fifteen_minutes(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    assert (booking.expires_at - booking.created_at).total_seconds() == 900
    assert service.cars["car-1"].status == "booked"
    assert service.customers[customer.id].bookings == (booking.id,)


def test_cancel_booking_is_free_for_first_five_minutes(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:05:00"):
        fee = service.cancel_booking(booking.id)
    assert fee == 0
    assert service.bookings[booking.id].status == "cancelled"
    assert service.cars["car-1"].status == "available"
    assert service.customers[customer.id].balance == 5000


def test_late_booking_cancellation_charges_fee(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:06:00"):
        fee = service.cancel_booking(booking.id)
    assert fee == 120
    assert customer.balance == 5000
    assert service.customers[customer.id].balance == 4880


def test_start_trip_from_booking(service: CarsharingService, customer) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:02:00"):
        trip = service.start_trip(customer.id, "car-1", booking_id=booking.id)
    assert trip.status == "active"
    assert booking.status == "active"
    assert service.bookings[booking.id].status == "used"
    assert service.cars[trip.car_id].status == "in_use"
    assert customer.trips == ()
    assert service.customers[customer.id].trips == (trip.id,)
    assert service.cars[trip.car_id].trips == (trip.id,)
    assert service.tariffs[trip.tariff_id].trips == (trip.id,)


def test_start_trip_rejects_blocked_customer(service: CarsharingService) -> None:
    customer = service.register_customer("c1", "Анна", "A123456", 100)
    service.customers[customer.id] = replace(customer, status="blocked")
    with pytest.raises(ValueError, match="заблокирован"):
        service.start_trip(customer.id, "car-1")


def test_calculate_day_cost_with_distance_and_idle(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    quote = service.calculate_trip_cost(
        trip.id, 3.0, idle_minutes=5, at=datetime(2025, 5, 12, 12, 10)
    )
    assert quote == 127.3
    assert calculate_cost(
        service.tariffs[trip.tariff_id], FarePeriod.DAY, 10, 3.0, 5
    ) == 134


def test_calculate_night_cost_applies_night_coefficient(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 23:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    quote = service.calculate_trip_cost(
        trip.id, 3.0, idle_minutes=5, at=datetime(2025, 5, 12, 23, 10)
    )
    assert quote == 190.95
    assert calculate_cost(
        service.tariffs[trip.tariff_id], FarePeriod.NIGHT, 10, 3.0, 5
    ) == 201


def test_promo_code_and_loyalty_discount(service: CarsharingService, customer) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    service.customers[customer.id] = replace(customer, loyalty_points=100)
    assert service.apply_promo_code(trip.id, 200, "WELCOME10") == 180
    assert service.apply_promo_code(trip.id, 200, "UNKNOWN") == 200
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
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    old_balance = customer.balance
    old_car = service.cars[trip.car_id]
    old_mileage = old_car.mileage
    with freeze_time("2025-05-12 12:10:00"):
        cost = service.finish_trip(trip.id, 3.0, (1.0, 1.0))
    assert cost == 117.8
    assert customer.balance == old_balance
    assert service.customers[customer.id].balance == old_balance - cost
    assert old_car.mileage == old_mileage
    assert old_car.status == "in_use"
    assert service.cars[trip.car_id].mileage == old_mileage + 3
    assert service.cars[trip.car_id].status == "available"
    assert service.trips[trip.id].status == "finished"
    assert service.total_finished == 1
    assert capsys.readouterr().out == f"Чек {trip.id}: 117.80 руб.\n"


def test_finish_outside_parking_zone_charges_fine(
    service: CarsharingService, customer, capsys
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    with freeze_time("2025-05-12 12:10:00"):
        amount = service.finish_trip(trip.id, 2, (15.0, 0.0))
    assert amount == 1500
    assert customer.balance == 5000
    assert service.customers[customer.id].balance == 3500
    assert service.trips[trip.id].status == "finished"
    assert "Штраф за парковку" in capsys.readouterr().out


def test_top_up_and_charge_balance(service: CarsharingService, customer) -> None:
    assert service.top_up_balance(customer.id, 200) == 5200
    assert service.charge_balance(customer.id, 50) == 5150
    assert customer.balance == 5000
    assert service.customers[customer.id].balance == 5150
    with pytest.raises(ValueError, match="положительным"):
        service.top_up_balance(customer.id, 0)


def test_negative_balance_blocks_customer_and_top_up_unblocks(
    service: CarsharingService,
) -> None:
    customer = service.register_customer("c1", "Анна", "A123456", 10)
    assert service.charge_balance(customer.id, 20) == -10
    assert customer.status == "active"
    assert service.customers[customer.id].status == "blocked"
    assert service.block_debtors() == (service.customers[customer.id],)
    service.top_up_balance(customer.id, 20)
    assert (customer.status, customer.balance) == ("active", 10)
    assert (
        service.customers[customer.id].status,
        service.customers[customer.id].balance,
    ) == ("active", 10)


def test_transfer_car_between_zones(service: CarsharingService) -> None:
    original_zone = service.zones["north"]
    original_car = service.cars["car-2"]
    car = service.transfer_car("car-2", "center")
    assert car.zone_id == "center"
    assert car.status == "available"
    assert original_zone.cars == ("car-2",)
    assert service.zones["north"].cars == ()
    assert service.zones["center"].cars == ("car-1", "car-3", "car-2")
    assert original_car.zone_id == "north"
    service.cars["car-2"] = replace(car, status="in_use")
    with pytest.raises(ValueError, match="свободный"):
        service.transfer_car("car-2", "north")


def test_economy_tariff_is_built_when_first_requested(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1", tariff_id="economy")
    assert service.tariffs["economy"].per_minute == 7
    assert trip.tariff_id == service.tariffs["economy"].id


def test_monthly_statement_includes_only_finished_trips_in_period(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    with freeze_time("2025-05-12 12:10:00"):
        service.finish_trip(trip.id, 1, (1.0, 1.0))
    statement = service.monthly_statement(customer.id, 2025, 5)
    assert statement["trip_count"] == 1
    assert statement["total"] == service.trips[trip.id].cost
    assert statement["period"] == "2025-05"
    assert service.monthly_statement(customer.id, 2025, 4)["trip_count"] == 0


def test_customer_trips_copy_on_write_preserves_original(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    assert customer.trips == ()
    assert service.customers[customer.id].trips == (trip.id,)
    assert isinstance(service.customers[customer.id].trips, tuple)
    with pytest.raises(FrozenInstanceError):
        customer.balance = 0
    assert customer.balance == 5000


def test_booking_cancel_after_expiry_raises_and_frees_car(
    service: CarsharingService, customer
) -> None:
    with freeze_time("2025-05-12 10:00:00"):
        booking = service.create_booking(customer.id, "car-1")
    with freeze_time("2025-05-12 10:16:00"):
        with pytest.raises(ValueError, match="истёк"):
            service.cancel_booking(booking.id)
    assert booking.status == "active"
    assert service.bookings[booking.id].status == "expired"
    assert service.cars["car-1"].status == "available"


def test_demand_coefficient_is_seeded_for_characterization(
    service: CarsharingService, customer
) -> None:
    service = CarsharingService(rng=random.Random(7))
    customer = service.register_customer("c1", "Анна", "A123456", 5000)
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    quote = service.calculate_trip_cost(
        trip.id, 1, at=datetime(2025, 5, 12, 12, 1)
    )
    assert quote == 18.54


def test_all_service_records_are_frozen_and_collections_are_tuples(
    service: CarsharingService, customer: Customer
) -> None:
    with freeze_time("2025-05-12 12:00:00"):
        trip = service.start_trip(customer.id, "car-1")
    records = (
        service.customers[customer.id],
        service.cars["car-1"],
        service.tariffs["standard"],
        service.trips[trip.id],
    )
    with pytest.raises(FrozenInstanceError):
        records[0].balance = 0
    with pytest.raises(FrozenInstanceError):
        records[1].status = "available"
    with pytest.raises(FrozenInstanceError):
        records[2].per_minute = 1
    with pytest.raises(FrozenInstanceError):
        records[3].status = "finished"
    assert isinstance(records[0].trips, tuple)
    assert isinstance(records[1].trips, tuple)
    assert isinstance(records[2].trips, tuple)


def test_tariff_factory_and_custom_settings_are_used(
    service: CarsharingService, customer: Customer
) -> None:
    tariff = Tariff.create("test", 3, 4, 1, 2)
    assert calculate_cost(tariff, FarePeriod.DAY, 10, 5, 2) == 52
    assert calculate_cost(tariff, FarePeriod.NIGHT, 10, 5, 2) == 104

    configured_service = CarsharingService(
        settings=Settings(booking_duration_minutes=30),
        rng=random.Random(1),
    )
    configured_service.register_customer("c1", "Анна", "A123456", 5000)
    with freeze_time("2025-05-12 10:00:00"):
        booking = configured_service.create_booking("c1", "car-1")
    assert (booking.expires_at - booking.created_at).total_seconds() == 1800


def test_pure_cancellation_fee_uses_only_explicit_values() -> None:
    created_at = datetime(2025, 5, 12, 10, 0)
    result = calculate_cancellation_fee(
        created_at,
        datetime(2025, 5, 12, 10, 15),
        datetime(2025, 5, 12, 10, 6),
        free_minutes=5,
        cancellation_fee=120,
        is_active=True,
    )
    assert (result.status, result.fee) == ("cancelled", 120)


def test_pure_trip_cost_and_loyalty_discount_do_not_mutate_customer() -> None:
    customer = Customer("c1", "Анна", "A123456", 1000, loyalty_points=100)
    tariff = Tariff.create("standard", 10, 8, 2, 1.5)
    result = calculate_trip_cost_pure(
        tariff,
        TripPricing(
            datetime(2025, 5, 12, 22),
            datetime(2025, 5, 12, 23, 10),
            3,
            5,
            1.0,
            "WELCOME10",
            customer.loyalty_points,
            True,
        ),
        23,
        6,
        1.0,
        500,
    )
    assert result.amount == 890.9
    assert (result.remaining_points, result.earned_points) == (0, 8)
    assert customer.balance == 1000
    assert customer.loyalty_points == 100
    assert apply_loyalty_discount(250, 100, True, 1, 50).amount == 200


def test_promo_discount_and_fine_are_pure_calculations() -> None:
    assert apply_promo_discount(
        100, "NIGHT20", datetime(2025, 5, 12, 22), 22, 5
    ) == 80
    assert calculate_fine(False, 1500) == 1500
    assert calculate_fine(True, 1500) == 0
