"""Clean up old, untagged images from an Amazon ECR repository.

Images pushed by CI pipelines accumulate over time. Layers that are no longer
tagged by any release are safe to remove once they are older than the retention
threshold. This tool lists every image in a repository, selects the ones that
have no tags and were pushed before the cutoff date, and deletes them.

The tool runs in dry-run mode by default: it prints what it WOULD delete and
does not touch anything. Pass --no-dry-run to actually delete images.
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
            "Delete untagged ECR images older than a threshold. "
            "Dry-run is enabled by default; pass --no-dry-run to delete."
        )
    )
    parser.add_argument(
        "--repository",
        required=True,
        help="Name of the ECR repository to clean up.",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Only consider images pushed more than this many days ago (default: 30).",
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


def list_images(client, repository):
    """Yield every image detail dict for a repository, handling pagination."""
    paginator = client.get_paginator("describe_images")
    for page in paginator.paginate(repositoryName=repository):
        for image in page.get("imageDetails", []):
            yield image


def select_candidates(images, cutoff):
    """Return images that are untagged AND pushed before the cutoff.

    Tagged images are never selected: a tag means some release or workflow
    still references that image digest.
    """
    candidates = []
    for image in images:
        tags = image.get("imageTags") or []
        pushed_at = image.get("imagePushedAt")
        if tags:
            continue
        if pushed_at is None:
            continue
        if pushed_at.replace(tzinfo=None) < cutoff:
            candidates.append(image)
    return candidates


def format_table(images):
    """Format the candidate list as a printable tabulate table."""
    rows = []
    for image in images:
        pushed = image.get("imagePushedAt")
        rows.append(
            {
                "Digest": (image.get("imageDigest") or "")[:19],
                "Pushed": pushed.strftime("%Y-%m-%d %H:%M") if pushed else "unknown",
                "Size MB": round(image.get("imageSizeInBytes", 0) / (1024 * 1024), 1),
            }
        )
    return tabulate(rows, headers="keys", tablefmt="github")


def delete_images(client, repository, images):
    """Delete the given images from the repository via batch_delete_image."""
    digests = [{"imageDigest": image["imageDigest"]} for image in images]
    deleted = 0
    for i in range(0, len(digests), 100):
        response = client.batch_delete_image(
            repositoryName=repository, imageIds=digests[i : i + 100]
        )
        deleted += len(response.get("imageIds", []))
    return deleted


def main(argv=None):
    """CLI entry point."""
    args = parse_args(argv)

    try:
        session_kwargs = {"region_name": args.region} if args.region else {}
        session = boto3.session.Session(**session_kwargs)
        client = session.client("ecr")
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
            f"Error: could not create the ECR client: {exc}. Check that your "
            "AWS credentials and region are configured, then retry.",
            file=sys.stderr,
        )
        return 1

    try:
        images = list(list_images(client, args.repository))
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
    candidates = select_candidates(images, cutoff)

    print(f"Repository: {args.repository}")
    print(f"Total images scanned: {len(images)}")
    print(f"Untagged images older than {args.older_than_days} days: {len(candidates)}")
    print()

    if not candidates:
        print("Nothing to clean up.")
        return 0

    print(format_table(candidates))
    print()

    if args.dry_run:
        print("Dry run: no images were deleted. Pass --no-dry-run to delete them.")
        return 0

    deleted = delete_images(client, args.repository, candidates)
    print(f"Deleted {deleted} image(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
