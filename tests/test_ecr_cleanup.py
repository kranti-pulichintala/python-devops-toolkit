"""Tests for toolkit.ecr_cleanup using a mocked boto3 ECR client."""

import datetime
from unittest.mock import MagicMock

import pytest

from toolkit import ecr_cleanup


def make_image(digest, tags, pushed_days_ago, size_bytes=1_000_000):
    """Build a fake describe_images imageDetails entry."""
    image = {
        "imageDigest": digest,
        "imagePushedAt": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        - datetime.timedelta(days=pushed_days_ago),
        "imageSizeInBytes": size_bytes,
    }
    if tags:
        image["imageTags"] = tags
    return image


@pytest.fixture
def mixed_images():
    """One old untagged image, one new untagged image, one old tagged image."""
    return [
        make_image("sha256:olduntagged", tags=None, pushed_days_ago=90),
        make_image("sha256:newuntagged", tags=None, pushed_days_ago=5),
        make_image("sha256:oldtagged", tags=["v1.2.3"], pushed_days_ago=120),
    ]


def make_client(images):
    """Return a mocked ECR client whose paginator yields a single page of images."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"imageDetails": images}]
    client = MagicMock()
    client.get_paginator.return_value = paginator
    return client


def test_old_untagged_images_selected(mixed_images):
    """Old, untagged images must be selected for deletion."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    candidates = ecr_cleanup.select_candidates(mixed_images, cutoff)
    digests = [c["imageDigest"] for c in candidates]
    assert digests == ["sha256:olduntagged"]


def test_tagged_images_never_selected(mixed_images):
    """A tagged image is never a deletion candidate, no matter how old it is."""
    cutoff = datetime.datetime.now() + datetime.timedelta(days=365)
    candidates = ecr_cleanup.select_candidates(mixed_images, cutoff)
    digests = [c["imageDigest"] for c in candidates]
    assert "sha256:oldtagged" not in digests


def test_dry_run_deletes_nothing(mixed_images, monkeypatch):
    """With the default --dry-run, batch_delete_image must never be called."""
    client = make_client(mixed_images)
    session = MagicMock()
    session.client.return_value = client
    monkeypatch.setattr("toolkit.ecr_cleanup.boto3.session.Session", lambda **kw: session)

    exit_code = ecr_cleanup.main(
        ["--repository", "my-app", "--older-than-days", "30"]
    )
    assert exit_code == 0
    client.batch_delete_image.assert_not_called()


def test_no_dry_run_deletes_candidates(mixed_images, monkeypatch, capsys):
    """With --no-dry-run, batch_delete_image is called with the selected digests."""
    client = make_client(mixed_images)
    client.batch_delete_image.return_value = {"imageIds": [{"imageDigest": "sha256:olduntagged"}]}
    session = MagicMock()
    session.client.return_value = client
    monkeypatch.setattr("toolkit.ecr_cleanup.boto3.session.Session", lambda **kw: session)

    exit_code = ecr_cleanup.main(
        ["--repository", "my-app", "--older-than-days", "30", "--no-dry-run"]
    )
    assert exit_code == 0
    client.batch_delete_image.assert_called_once()
    _, kwargs = client.batch_delete_image.call_args
    assert kwargs["repositoryName"] == "my-app"
    assert kwargs["imageIds"] == [{"imageDigest": "sha256:olduntagged"}]
    assert "Deleted 1 image(s)." in capsys.readouterr().out


def test_format_table_shows_short_digest(mixed_images):
    """The printed table must contain a truncated digest and size columns."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    candidates = ecr_cleanup.select_candidates(mixed_images, cutoff)
    table = ecr_cleanup.format_table(candidates)
    assert "Digest" in table
    assert "Size MB" in table
    assert "sha256:olduntagged"[:19] in table
