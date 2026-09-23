"""devops-toolkit: small, practical AWS maintenance and cost-visibility CLI tools.

Each tool is a standalone command-line entry point:

- ecr_cleanup: remove old, untagged ECR images to reclaim repository space.
- stale_snapshots: find and delete orphaned EBS snapshots.
- cost_summary: print a top-10 services cost summary from AWS Cost Explorer.
"""

__version__ = "0.1.0"
