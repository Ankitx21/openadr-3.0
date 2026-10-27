"""Compare equal-energy schedules locally; never upload forecasts as measured data."""
import argparse
try:
    from openadr3_client import TeamVen
except ImportError:
    from team_kit.openadr3_client import TeamVen


def run_simulation(token, server_url, appliance_type='Smart_Air_Conditioner', send_test=False):
    ven = TeamVen(token, server_url, appliance_type)
    tariffs = ven.get_tariffs()
    baseline = optimized = 0
    for day in (tariffs[:24], tariffs[24:]):
        baseline += sum(t['price_per_kwh'] * 1.75 for t in day[17:23])
        optimized += sum(t['price_per_kwh'] * 1.75 for t in sorted(day, key=lambda t: t['price_per_kwh'])[:6])
    print('Simulation: 1.75 kW for six hours each day (21 kWh over 48 hours).')
    print(f'Evening schedule: INR {baseline:.2f}; cheapest-hour schedule: INR {optimized:.2f}')
    print(f'Illustrative savings: INR {baseline-optimized:.2f}. Real appliance constraints may limit shifting.')
    print('Forecasts stay local and are not submitted as baseline or prototype measurements.')
    if send_test:
        ven.send_testing(energy=0.25, appliance=appliance_type, notes='Synthetic connection test; not measured consumption.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token', required=True)
    parser.add_argument('--server', default='http://127.0.0.1:8000')
    parser.add_argument('--appliance', default='Smart_Air_Conditioner')
    parser.add_argument('--send-test', action='store_true')
    args = parser.parse_args()
    run_simulation(args.token, args.server, args.appliance, args.send_test)
