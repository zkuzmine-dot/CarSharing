"""Mutable global settings: intentionally implicit configuration."""


class Config:
    cancellation_free_minutes = 5
    booking_duration_minutes = 15
    cancellation_fee = 120.0
    parking_fine = 1500.0
    demand_minimum = 0.9
    demand_maximum = 1.3
    night_start_hour = 23
    night_end_hour = 6
    loyalty_discount_per_point = 1.0
    maximum_loyalty_discount = 500.0


config = Config()
