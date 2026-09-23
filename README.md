# python-devops-toolkit

A small collection of practical AWS maintenance and cost-visibility CLI tools, built as a reference architecture for the routine cleanup work that keeps cloud bills and container registries under control.

## Why this matters

AWS accounts collect clutter: CI pipelines push ECR images on every commit, developers leave EBS snapshots behind after deleting volumes, and spend drifts upward because nobody looks at Cost Explorer on a schedule. These three tools automate the boring, high-leverage parts of that housekeeping:

- **ecr-cleanup**: removes old, untagged ECR images so repositories stop growing forever.
- **stale-snapshots**: finds EBS snapshots that no longer back a volume or an AMI, a direct line item on the storage bill.
- **cost-summary**: prints a top-10 services cost summary from Cost Explorer for quick daily review.

Every mutating tool runs in **dry-run mode by default** and only deletes when you explicitly pass `--no-dry-run`. Cleanup should never be a surprise.

## Prerequisites

- Python 3.11 or newer.
- An AWS account with credentials configured (environment variables, shared credentials file, or instance profile).
- IAM permissions for the APIs each tool calls:
  - `ecr-cleanup`: `ecr:DescribeImages`, `ecr:BatchDeleteImage`
  - `stale-snapshots`: `ec2:DescribeVolumes`, `ec2:DescribeSnapshots`, `ec2:DescribeImages`, `ec2:DeleteSnapshot`
  - `cost-summary`: `ce:GetCostAndUsage`

## Install

```bash
git clone <repo-url>
cd python-devops-toolkit
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development (lint and tests):

```bash
pip install -e .[dev]
```

## Usage

### ecr-cleanup

List old, untagged images without deleting anything:

```bash
ecr-cleanup --repository my-app --older-than-days 30
```

Sample output:

```
Repository: my-app
Total images scanned: 128
Untagged images older than 30 days: 3

| Digest              | Pushed           |   Size MB |
|---------------------|------------------|-----------|
| sha256:9f2c1a4be7   | 2026-07-11 03:12 |      84.2 |
| sha256:44d0b8c2f1   | 2026-07-08 22:45 |     112.7 |
| sha256:01e77aa9d3   | 2026-06-29 14:03 |      84.2 |

Dry run: no images were deleted. Pass --no-dry-run to delete them.
```

Actually delete the selected images:

```bash
ecr-cleanup --repository my-app --older-than-days 30 --no-dry-run
```

Tagged images are never selected: a tag means some release or workflow still references that digest.

### stale-snapshots

Find snapshots that are not attached to a live volume and not referenced by any AMI:

```bash
stale-snapshots --older-than-days 30
```

Sample output:

```
Snapshots scanned: 214
Live volumes: 41 | Snapshots used by AMIs: 12
Stale snapshots older than 30 days: 2

| Snapshot ID         |   Size GiB |   Age (days) | Reason               |
|---------------------|------------|--------------|----------------------|
| snap-0a1b2c3d4e5f6  |         50 |           96 | source volume deleted|
| snap-1b2c3d4e5f6a7  |        100 |          142 | source volume deleted|

Dry run: no snapshots were deleted. Pass --no-dry-run to delete them.
```

Delete them once you have reviewed the list:

```bash
stale-snapshots --older-than-days 30 --no-dry-run --region us-east-1
```

### cost-summary

Print the top 10 services by unblended cost for the last 30 days:

```bash
cost-summary
```

Sample output:

```
AWS cost summary: 2026-08-23 to 2026-09-22 (UnblendedCost, grouped by service)

| Service                              | Cost ($)   |
|--------------------------------------|------------|
| Amazon Elastic Compute Cloud - Compute| 1,842.17  |
| Amazon Relational Database Service   |   612.44   |
| Amazon Simple Storage Service        |   188.90   |
| AWS Cost Explorer                    |     0.00   |

Total across 4 services: $2,643.51
```

Show the top 20 services instead of 10:

```bash
cost-summary --top 20
```

### Getting help

Each tool documents its own flags:

```bash
ecr-cleanup --help
stale-snapshots --help
cost-summary --help
```

## File layout

```
python-devops-toolkit/
  pyproject.toml                 Project metadata, CLI entry points, ruff config
  README.md                      This file
  src/
    toolkit/
      __init__.py                Package version and overview docstring
      ecr_cleanup.py             Delete old untagged ECR images (dry-run by default)
      stale_snapshots.py         Find and delete orphaned EBS snapshots (dry-run by default)
      cost_summary.py            Top-10 AWS services cost summary from Cost Explorer
  tests/
    test_ecr_cleanup.py          pytest tests for ecr_cleanup with mocked boto3 client
  .github/
    workflows/
      ci.yml                     GitHub Actions: install, ruff check, pytest on 3.11 and 3.12
```
