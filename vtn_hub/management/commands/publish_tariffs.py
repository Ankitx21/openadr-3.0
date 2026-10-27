from __future__ import annotations

import argparse
from django.core.management.base import BaseCommand, CommandError
from django.core.cache import cache
from vtn_hub.tariffs import generate_dynamic_tariffs, save_published_tariffs


class Command(BaseCommand):
    help = "Generate and publish dynamic 48-hour Time-of-Use tariffs within [min_price, max_price] using a random seed."

    def add_arguments(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Integer random seed (if omitted, a random seed is selected)",
        )
        parser.add_argument(
            "--min-price",
            type=float,
            default=8.0,
            help="Minimum tariff price in INR/kWh (default: 8.0)",
        )
        parser.add_argument(
            "--max-price",
            type=float,
            default=20.0,
            help="Maximum tariff price in INR/kWh (default: 20.0)",
        )

    def handle(self, *args, **options):
        seed = options.get("seed")
        min_p = options.get("min_price", 8.0)
        max_p = options.get("max_price", 20.0)

        try:
            points, seed_used = generate_dynamic_tariffs(seed=seed, min_price=min_p, max_price=max_p)
        except ValueError as exc:
            raise CommandError(str(exc))
        if not cache.add('tariff-rollover-lock', True, 15):
            raise CommandError('Another tariff update is in progress. Retry shortly.')
        try:
            save_published_tariffs(points, seed=seed_used)
        finally:
            cache.delete('tariff-rollover-lock')

        prices = [p["price_per_kwh"] for p in points]
        avg_price = sum(prices) / len(prices)

        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(f"Successfully Published 48-Hour Dynamic Time-of-Use Tariffs"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f"Random Seed Used : {seed_used}")
        self.stdout.write(f"Total Intervals  : {len(points)} Hours (Today + Tomorrow)")
        self.stdout.write(f"Price Bounds     : INR {min(prices):.2f} - INR {max(prices):.2f} / kWh (Target: [{min_p:.2f}, {max_p:.2f}])")
        self.stdout.write(f"Average Price    : INR {avg_price:.2f} / kWh")
        self.stdout.write("=" * 60)
