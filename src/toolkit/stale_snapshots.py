"""Find and delete stale EBS snapshots to cut storage cost.

EBS snapshots are billed per GB-month until they are deleted. Snapshots that
no longer back a volume or an AMI are almost always safe to remove. This tool
collects every snapshot owned by the account, marks the ones that are not
attached to a live volume and not referenced by any AMI, filters by age, and
deletes them.

The tool runs in dry-run mode by default: it prints what it WOULD delete and
does not touch anything. Pass --no-dry-run to actually delete snapshots.
"""

import argparse
import datetime
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError
from tabulate import tabulate


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Find EBS snapshots not attached to a volume or AMI and optionally "
            "delete them. Dry-run is enabled by default; pass --no-dry-run to delete."
        )
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Only consider snapshots older than this many days (default: 30).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print what would be deleted without deleting (default: --dry-run).",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (defaults to the usual boto3 resolution chain).",
    )
    return parser.parse_args(argv)


def live_volume_ids(client):
    """Return the set of all current EBS volume IDs in the region."""
    volume_ids = set()
    paginator = client.get_paginator("describe_volumes")
    for page in paginator.paginate():
        for volume in page.get("Volumes", []):
            volume_ids.add(volume["VolumeId"])
    return volume_ids


def ami_snapshot_ids(client):
    """Return the set of snapshot IDs referenced by AMIs owned by this account."""
    snapshot_ids = set()
    paginator = client.get_paginator("describe_images")
    for page in paginator.paginate(Owners=["self"]):
        for image in page.get("Images", []):
            for device in image.get("BlockDeviceMappings", []):
                ebs = device.get("Ebs") or {}
                if ebs.get("SnapshotId"):
                    snapshot_ids.add(ebs["SnapshotId"])
    return snapshot_ids


def select_stale(snapshots, volume_ids, ami_snapshots, cutoff):
    """Return (stale_snapshots, reasons) for snapshots that are unused and old.

    A snapshot is considered stale when its source volume no longer exists
    (VolumeId absent or not a live volume) and no AMI references it.
    """
    stale = []
    for snapshot in snapshots:
        start_time = snapshot.get("StartTime")
        if start_time is None:
            continue
        if start_time.replace(tzinfo=None) >= cutoff:
            continue
        snapshot_id = snapshot["SnapshotId"]
        if snapshot_id in ami_snapshots:
            continue
        volume_id = snapshot.get("VolumeId")
        if volume_id in volume_ids:
            continue
        reason = (
            "source volume deleted"
            if volume_id not in volume_ids
            else "no volume attachment"
        )
        stale.append((snapshot, reason))
    return stale


def format_table(stale):
    """Format the stale snapshot list as a printable tabulate table."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    rows = []
    for snapshot, reason in stale:
        age_days = (now - snapshot["StartTime"].replace(tzinfo=None)).days
        rows.append(
            {
                "Snapshot ID": snapshot["SnapshotId"],
                "Size GiB": snapshot.get("VolumeSize", 0),
                "Age (days)": age_days,
                "Reason": reason,
            }
        )
    return tabulate(rows, headers="keys", tablefmt="github")


def main(argv=None):
    """CLI entry point."""
    args = parse_args(argv)

    try:
        session_kwargs = {"region_name": args.region} if args.region else {}
        session = boto3.session.Session(**session_kwargs)
        ec2 = session.client("ec2")
    except NoCredentialsError:
        print(
            "Error: no AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY (or configure a profile) and try again.",
            file=sys.stderr,
        )
        return 1
    except NoRegionError:
        print(
            "Error: no AWS region configured. Pass --region or set AWS_REGION.",
            file=sys.stderr,
        )
        return 1
    except BotoCoreError as exc:
        print(
            f"Error: could not create the EC2 client: {exc}. Check that your "
            "AWS credentials and region are configured, then retry.",
            file=sys.stderr,
        )
        return 1

    try:
        volume_ids = live_volume_ids(ec2)
        ami_snapshots = ami_snapshot_ids(ec2)

        snapshots = []
        paginator = ec2.get_paginator("describe_snapshots")
        for page in paginator.paginate(OwnerIds=["self"]):
            snapshots.extend(page.get("Snapshots", []))
    except NoCredentialsError:
        print(
            "Error: no AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY (or configure a profile) and try again.",
            file=sys.stderr,
        )
        return 1
    except NoRegionError:
        print(
            "Error: no AWS region configured. Pass --region or set AWS_REGION.",
            file=sys.stderr,
        )
        return 1
    except ClientError as exc:
        print(f"Error: AWS request failed: {exc}", file=sys.stderr)
        return 1
    except BotoCoreError as exc:
        print(
            f"Error: AWS request could not be completed: {exc}. Check that your "
            "AWS credentials and region are configured, then retry.",
            file=sys.stderr,
        )
        return 1

    cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - (
        datetime.timedelta(days=args.older_than_days)
    )
    stale = select_stale(snapshots, volume_ids, ami_snapshots, cutoff)

    print(f"Snapshots scanned: {len(snapshots)}")
    print(f"Live volumes: {len(volume_ids)} | Snapshots used by AMIs: {len(ami_snapshots)}")
    print(f"Stale snapshots older than {args.older_than_days} days: {len(stale)}")
    print()

    if not stale:
        print("Nothing to clean up.")
        return 0

    print(format_table(stale))
    print()

    if args.dry_run:
        print("Dry run: no snapshots were deleted. Pass --no-dry-run to delete them.")
        return 0

    deleted = 0
    for snapshot, _reason in stale:
        try:
            ec2.delete_snapshot(SnapshotId=snapshot["SnapshotId"])
            deleted += 1
        except ClientError as exc:
            print(f"Warning: could not delete {snapshot['SnapshotId']}: {exc}", file=sys.stderr)
    print(f"Deleted {deleted} snapshot(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
