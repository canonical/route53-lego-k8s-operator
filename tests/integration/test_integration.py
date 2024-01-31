#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

TLS_REQUIRER_CHARM_NAME = "tls-certificates-requirer"


@pytest.fixture(scope="module")
@pytest.mark.abort_on_fail
async def build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it."""
    charm = await ops_test.build_charm(".")
    resources = {"lego-image": METADATA["resources"]["lego-image"]["upstream-source"]}
    assert ops_test.model
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        series="jammy",
        config={
            "email": "example@email.com",
            "aws_access_key_id": "dummy key",
            "aws_secret_access_key": "dummy access key",
            "aws_region": "dummy region",
            "aws_hosted_zone_id": "dummy zone id",
        },
    )
    await ops_test.model.deploy(
        TLS_REQUIRER_CHARM_NAME,
        application_name=TLS_REQUIRER_CHARM_NAME,
        channel="edge",
    )


@pytest.mark.abort_on_fail
async def test_given_charm_is_built_when_deployed_then_status_is_active(
    ops_test: OpsTest,
    build_and_deploy,
):
    assert ops_test.model
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="active",
        timeout=1000,
    )


async def test_given_tls_requirer_is_deployed_and_related_then_status_is_active(
    ops_test: OpsTest,
    build_and_deploy,
):
    assert ops_test.model
    await ops_test.model.add_relation(
        relation1=f"{APP_NAME}:certificates", relation2=f"{TLS_REQUIRER_CHARM_NAME}"
    )
    await ops_test.model.wait_for_idle(
        apps=[TLS_REQUIRER_CHARM_NAME],
        status="active",
        timeout=1000,
    )