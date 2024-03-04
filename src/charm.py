#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Retrieves certificates from an ACME server using the AWS Route53 dns provider."""

import logging
from typing import Dict

from charms.lego_base_k8s.v0.lego_client import AcmeClient
from ops.framework import EventBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

logger = logging.getLogger(__name__)


class Route53LegoK8s(AcmeClient):
    """Main class that is instantiated every time an event occurs."""

    REQUIRED_CONFIG = [
        "AWS_REGION",
        "AWS_HOSTED_ZONE_ID",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ]

    def __init__(self, *args):
        """Use the lego_client library to manage events."""
        super().__init__(*args, plugin="route53")
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    @property
    def _aws_access_key_id(self) -> str:
        """Return aws access key from config."""
        return self.model.config.get("aws_access_key_id", "")

    @property
    def _aws_hosted_zone_id(self) -> str:
        """Return aws hosted zone id from config."""
        return self.model.config.get("aws_hosted_zone_id", "")

    @property
    def _aws_secret_access_key(self) -> str:
        """Return aws secret access key from config."""
        return self.model.config.get("aws_secret_access_key", "")

    @property
    def _aws_region(self) -> str:
        """Returns aws region from config."""
        return self.model.config.get("aws_region", "")

    @property
    def _aws_max_retries(self) -> str:
        """Returns aws max retries from config."""
        return str(self.model.config.get("aws_max_retries"))

    @property
    def _aws_polling_interval(self) -> str:
        """Returns aws polling interval from config."""
        return str(self.model.config.get("aws_polling_interval"))

    @property
    def _aws_propagation_timeout(self) -> str:
        """Returns aws propagation timeout from config."""
        return str(self.model.config.get("aws_propagation_timeout"))

    @property
    def _aws_ttl(self) -> str:
        """Returns aws ttl from config."""
        return str(self.model.config.get("aws_ttl"))

    @property
    def _plugin_config(self) -> Dict[str, str]:
        """Plugin specific additional configuration for the command."""
        additional_config = {
            "AWS_REGION": self._aws_region,
            "AWS_HOSTED_ZONE_ID": self._aws_hosted_zone_id,
            "AWS_ACCESS_KEY_ID": self._aws_access_key_id,
            "AWS_SECRET_ACCESS_KEY": self._aws_secret_access_key,
        }
        if self._aws_max_retries:
            additional_config["AWS_MAX_RETRIES"] = self._aws_max_retries
        if self._aws_polling_interval:
            additional_config["AWS_POLLING_INTERVAL"] = self._aws_polling_interval
        if self._aws_propagation_timeout:
            additional_config["AWS_PROPAGATION_TIMEOUT"] = self._aws_propagation_timeout
        if self._aws_ttl:
            additional_config["AWS_TTL"] = self._aws_ttl
        return additional_config

    def _on_config_changed(self, event: EventBase) -> None:
        """Handle config-changed events."""
        if not self._validate_route53_config():
            return
        if not self.validate_generic_acme_config():
            return
        self.unit.status = ActiveStatus()

    def _validate_route53_config(self) -> bool:
        """Check whether required config options are set.

        Returns:
            bool: True/False
        """
        if missing_config := [
            option for option in self.REQUIRED_CONFIG if not self._plugin_config[option]
        ]:
            msg = f"The following config options must be set: {', '.join(missing_config)}"
            self.unit.status = BlockedStatus(msg)
            return False
        return True


if __name__ == "__main__":  # pragma: nocover
    main(Route53LegoK8s)
