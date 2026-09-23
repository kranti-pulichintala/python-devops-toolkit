"""Print an AWS cost summary grouped by service for the last 30 days.

Cost Explorer is the authoritative source for AWS spend, but the console view
is slow for a quick daily glance. This tool pulls the last 30 days of
unblended cost with monthly granularity, groups it by service, handles the
NextPageToken pagination itself, and prints the top 10 services plus a total.
"""

import argparse
import datetime
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError
from tabulate import tabulate

TOP_N = 10


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Show AWS cost for the last 30 days, grouped by service (top 10)."
    )
    parser.add_argument(
        "--top",
        type=int,
        default=TOP_N,
        help=f"Number of top services to display (default: {TOP_N}).",
    )
    return parser.parse_args(argv)


def date_range(days=30):
    """Return (start, end) date strings for the trailing window."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def fetch_costs(client, start, end):
    """Return {service_name: cost} for the window, following NextPageToken pages."""
    costs = {}
    next_token = None
    while True:
        kwargs = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
        }
        if next_token:
            kwargs["NextPageToken"] = next_token
        response = client.get_cost_and_usage(**kwargs)
        for result in response.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                costs[service] = costs.get(service, 0.0) + amount
        next_token = response.get("NextPageToken")
        if not next_token:
            break
    return costs


def format_table(costs, top):
    """Format the top services as a printable tabulate table."""
    rows = [
        {"Service": service, "Cost ($)": f"{amount:,.2f}"}
        for service, amount in sorted(costs.items(), key=lambda kv: kv[1], reverse=True)[:top]
    ]
    return tabulate(rows, headers="keys", tablefmt="github")


def main(argv=None):
    """CLI entry point."""
    args = parse_args(argv)

    try:
        client = boto3.session.Session().client("ce", region_name="us-east-1")
    except NoCredentialsError:
        print(
            "Error: no AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY (or configure a profile) and try again.",
            file=sys.stderr,
        )
        return 1
    except NoRegionError:
        print("Error: no AWS region configured. Set AWS_REGION and try again.", file=sys.stderr)
        return 1
    except BotoCoreError as exc:
        print(
            f"Error: could not create the Cost Explorer client: {exc}. Check that "
            "your AWS credentials are configured, then retry.",
            file=sys.stderr,
        )
        return 1

    start, end = date_range()

    try:
        costs = fetch_costs(client, start, end)
    except NoCredentialsError:
        print(
            "Error: no AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY (or configure a profile) and try again.",
            file=sys.stderr,
        )
        return 1
    except NoRegionError:
        print("Error: no AWS region configured. Set AWS_REGION and try again.", file=sys.stderr)
        return 1
    except ClientError as exc:
        print(f"Error: AWS request failed: {exc}", file=sys.stderr)
        return 1
    except BotoCoreError as exc:
        print(
            f"Error: AWS request could not be completed: {exc}. Check that your "
            "AWS credentials are configured, then retry.",
            file=sys.stderr,
        )
        return 1

    print(f"AWS cost summary: {start} to {end} (UnblendedCost, grouped by service)")
    print()

    if not costs:
        print("No cost data returned for this period.")
        return 0

    print(format_table(costs, args.top))
    print()
    total = sum(costs.values())
    print(f"Total across {len(costs)} services: ${total:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
