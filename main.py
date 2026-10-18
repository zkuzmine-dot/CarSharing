"""Deterministic console walkthrough of the initial car-sharing application."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime

from carsharing.service import CarsharingService


def _run_scenario() -> None:
    service = CarsharingService(
        clock=lambda: datetime(2025, 5, 12, 12, 0, 0),
        rng=random.Random(7),
    )

    customer = service.register_customer(
        "customer-1", "Анна Иванова", "77АА123456", balance=5000
    )
    customer = replace(customer, loyalty_points=50)
    service.customers[customer.id] = customer
    print(f"Регистрация: {customer.name}, права проверены")

    nearest = service.find_nearest_cars((1.0, 1.0), limit=2)
    print("Ближайшие машины:", ", ".join(car.model for car in nearest))

    booking = service.create_booking(customer.id, "car-1")
    print(
        f"Бронь {booking.id}: до "
        f"{booking.expires_at.strftime('%H:%M')}, срок 15 минут"
    )
    print(f"Отмена брони {booking.id}: комиссия {service.cancel_booking(booking.id):.2f}")

    second_booking = service.create_booking(customer.id, "car-1")
    trip = service.start_trip(customer.id, "car-1", booking_id=second_booking.id)
    print(
        f"Начало поездки {trip.id}: {service.cars[trip.car_id].model}, "
        f"тариф {service.tariffs[trip.tariff_id].id}"
    )

    quote = service.calculate_trip_cost(
        trip.id,
        distance=8.5,
        idle_minutes=4,
        promo_code="WELCOME10",
        use_loyalty=True,
        at=datetime(2025, 5, 12, 14, 30),
    )
    print(f"Предварительный расчёт: {quote:.2f} руб.")
    final_cost = service.finish_trip(
        trip.id,
        distance=8.5,
        parking_point=(1.0, 1.0),
        idle_minutes=4,
        promo_code="WELCOME10",
        use_loyalty=True,
    )
    print(f"Завершение поездки: {final_cost:.2f} руб.")

    restricted_trip = service.start_trip(customer.id, "car-3", "comfort")
    penalty = service.finish_trip(
        restricted_trip.id, 2.0, parking_point=(15.0, 0.0)
    )
    print(f"Парковка вне разрешённой зоны: штраф {penalty:.2f} руб.")

    balance = service.top_up_balance(customer.id, 2000)
    print(f"Пополнение: баланс {balance:.2f} руб.")
    balance = service.charge_balance(customer.id, 100)
    print(f"Списание: баланс {balance:.2f} руб.")
    customer = service.customers[customer.id]

    debtor = service.register_customer(
        "customer-2", "Борис Петров", "77ББ654321", balance=10
    )
    service.charge_balance(debtor.id, 20)
    print("Заблокированные должники:", ", ".join(
        customer.name for customer in service.block_debtors()
    ))
    service.top_up_balance(debtor.id, 50)
    debtor = service.customers[debtor.id]
    print(f"После погашения долга: {debtor.status}, баланс {debtor.balance:.2f}")

    transferred = service.transfer_car("car-2", "center")
    print(
        f"Перегон: {transferred.model} теперь в зоне "
        f"{service.zones[transferred.zone_id].name}"
    )
    statement = service.monthly_statement(customer.id, 2025, 5)
    print(
        f"Выписка {statement['period']}: поездок {statement['trip_count']}, "
        f"расход {statement['total']:.2f} руб."
    )


def main() -> None:
    _run_scenario()


if __name__ == "__main__":
    main()
