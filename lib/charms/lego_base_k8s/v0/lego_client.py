# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""# lego_client Library.

Deprecation Notice: This library is deprecated. Please use the lego charm instead: https://charmhub.io/lego.

This library is designed to enable developers to easily create new charms for the ACME protocol.
This library contains all the logic necessary to get certificates from an ACME server.

## Getting Started
To get started using the library, you need to fetch the library using `charmcraft`.
```shell
charmcraft fetch-lib charms.lego_client_operator.v0.lego_client
```

You will also need the following libraries:

```shell
charmcraft fetch-lib charms.tls_certificates_interface.v4.tls_certificates
charmcraft fetch-lib charms.certificate_transfer_interface.v1.certificate_transfer
charmcraft fetch-lib charms.loki_k8s.v1.loki_push_api
```

You will also need to add the following library to the charm's `requirements.txt` file:
- jsonschema
- cryptography
- cosl

Then, to use the library in an example charm, you can do the following:
```python
from charms.lego_client_operator.v0.lego_client import AcmeClient
from ops.main import main
class ExampleAcmeCharm(AcmeClient):
    def __init__(self, *args):
        super().__init__(*args, plugin="namecheap")
        self._server = "https://acme-staging-v02.api.letsencrypt.org/directory"

    def _validate_plugin_config(self) -> str:
        if not self._api_key:
            return "API key was not provided"
        return ""

    @property
    def _plugin_config(self):
        return {}
```

Charms using this library are expected to:
- Inherit from AcmeClient
- Call `super().__init__(*args, plugin="")` with the lego plugin name
- Observe `ConfigChanged` to a method called `_on_config_changed`
- Implement the `_validate_plugin_config` method,
  it should validate the plugin specific configuration,
  returning a string with an error message if the
  plugin specific configuration is invalid, otherwise an empty string.
- Implement the `_plugin_config` property, returning a dictionary of its specific
  configuration. Keys must be capitalized and follow the plugins documentation from
  lego.
- Specify a `certificates` integration in their
  `metadata.yaml` file:
```yaml
provides:
  certificates:
    interface: tls-certificates
  send-ca-cert:
    interface: certificate_transfer
```
- Specify a `logging` integration in their `metadata.yaml` file:
```yaml
requires:
  logging:
    interface: loki_push_api
```
"""

import abc
import logging
import os
import re
from abc import abstractmethod
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from charms.certificate_transfer_interface.v1.certificate_transfer import (
    CertificateTransferProvides,
)
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.tls_certificates_interface.v4.tls_certificates import (
    Certificate,
    CertificateSigningRequest,
    ProviderCertificate,
    TLSCertificatesProvidesV4,
)
from ops.charm import CharmBase, CollectStatusEvent
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ExecError

# The unique Charmhub library identifier, never change it
LIBID = "d67f92a288e54ab68a6b6349e9b472c4"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 17


logger = logging.getLogger(__name__)

CERTIFICATES_RELATION_NAME = "certificates"
CA_TRANSFER_RELATION_NAME = "send-ca-cert"


class AcmeClient(CharmBase):
    """Base charm for charms that use the ACME protocol to get certificates.

    This charm implements the tls_certificates interface as a provider.
    """

    __metaclass__ = abc.ABCMeta

    def __init__(self, *args: Any, plugin: str):
        super().__init__(*args)
        logger.warning("This library is deprecated. Please use the lego charm instead.")
        self._csr_path = "/tmp/csr.pem"
        self._certs_path = "/tmp/.lego/certificates/"
        self._container_name = list(self.meta.containers.values())[0].name
        self._container = self.unit.get_container(self._container_name)
        self._logging = LogForwarder(self, relation_name="logging")
        self.tls_certificates = TLSCertificatesProvidesV4(self, CERTIFICATES_RELATION_NAME)
        self.cert_transfer = CertificateTransferProvides(self, CA_TRANSFER_RELATION_NAME)
        self.framework.observe(self.on.send_ca_cert_relation_joined, self._configure)
        self.framework.observe(
            self.on[CERTIFICATES_RELATION_NAME].relation_changed, self._configure
        )
        self.framework.observe(self.on.config_changed, self._configure)
        self.framework.observe(self.on.update_status, self._configure)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        self._plugin = plugin

    def _on_collect_status(self, event: CollectStatusEvent) -> None:
        """Handle the collect status event."""
        if not self._container.can_connect():
            event.add_status(WaitingStatus("Waiting to be able to connect to LEGO container"))
            return

        if err := self.validate_generic_acme_config():
            event.add_status(BlockedStatus(err))
            return
        if err := self._validate_plugin_config():
            event.add_status(BlockedStatus(err))
            return
        event.add_status(ActiveStatus(self._get_certificate_fulfillment_status()))

    def _configure(self, event: EventBase) -> None:
        """Configure the Lego provider.

        Validate configs.
        Go through all the certificates relations and handle outstanding requests.
        Go Through all certificate transfer relations and share the CA certificates.
        """
        if not self._container.can_connect():
            return
        if err := self.validate_generic_acme_config():
            logger.error(err)
            return
        if err := self._validate_plugin_config():
            logger.error(err)
            return
        for relation in self.model.relations.get(CERTIFICATES_RELATION_NAME, []):
            outstanding_requests = self.tls_certificates.get_outstanding_certificate_requests(
                relation_id=relation.id
            )
            for request in outstanding_requests:
                self._generate_signed_certificate(
                    csr=request.certificate_signing_request,
                    relation_id=relation.id,
                )
        if self._is_relation_created(CA_TRANSFER_RELATION_NAME):
            self.cert_transfer.add_certificates(self._get_issuing_ca_certificates())

    @abstractmethod
    def _validate_plugin_config(self) -> str:
        """Validate plugin specific configuration.

        Implementations need to validate the plugin specific configuration
        And return either an empty string if valid
        Or an the status message if invalid.

        Returns:
        str: Error message if invalid, otherwise an empty string.
        """
        pass

    def validate_generic_acme_config(self) -> str:
        """Validate generic ACME config.

        Returns:
        str: Error message if invalid, otherwise an empty string.
        """
        if not self._email:
            return "Email address was not provided"
        if not self._server:
            return "ACME server was not provided"
        if not self._email_is_valid(self._email):
            return "Invalid email address"
        if not self._server_is_valid(self._server):
            return "Invalid ACME server"
        return ""

    def _push_csr_to_workload(self, csr: CertificateSigningRequest) -> None:
        """Push CSR to workload container."""
        self._container.push(path=self._csr_path, make_dirs=True, source=str(csr))

    def _execute_lego_cmd(self) -> bool:
        """Execute lego command in workload container."""
        if app_env := self._app_environment:
            logger.info("Running the Lego command with %s environment variables", app_env)
        process = self._container.exec(
            self._cmd,
            timeout=300,
            working_dir="/tmp",
            environment=app_env | self._plugin_config,
        )
        try:
            stdout, error = process.wait_output()
            logger.info("Return message: %s, %s", stdout, error)
        except ExecError as e:
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():  # type: ignore
                logger.error("    %s", line)
            return False
        return True

    def _pull_certificates_from_workload(self, csr_subject: str) -> List[str]:
        """Pull certificates from workload container."""
        chain_pem = self._container.pull(path=f"{self._certs_path}{csr_subject}.crt")
        return list(chain_pem.read().split("\n\n"))

    def _generate_signed_certificate(self, csr: CertificateSigningRequest, relation_id: int):
        """Generate signed certificate from the ACME provider."""
        if not self.unit.is_leader():
            logger.debug("Only the leader can handle certificate requests")
            return
        if not self._container.can_connect():
            logger.info("Container is not ready")
            return
        logger.info("Received Certificate Creation Request for domain %s", csr.common_name)
        self._push_csr_to_workload(csr=csr)
        if not self._execute_lego_cmd():
            logger.error(
                "Failed to execute lego command \
                will try again in during the next update status event."
            )
            return
        if not (signed_certificates := self._pull_certificates_from_workload(csr.common_name)):
            logger.error(
                "Failed to pull certificates from workload \
                will try again in during the next update status event."
            )
            return
        self.tls_certificates.set_relation_certificate(
            provider_certificate=ProviderCertificate(
                certificate=Certificate.from_string(signed_certificates[0]),
                certificate_signing_request=csr,
                ca=Certificate.from_string(signed_certificates[-1]),
                chain=[Certificate.from_string(cert) for cert in reversed(signed_certificates)],
                relation_id=relation_id,
            ),
        )

    def _get_issuing_ca_certificates(self) -> Set[str]:
        """Get a list of the CA certificates that have been used with the issued certs."""
        return {
            str(provider_certificate.ca)
            for provider_certificate in self.tls_certificates.get_provider_certificates()
        }

    def _get_certificate_fulfillment_status(self) -> str:
        """Return the status message reflecting how many certificate requests are still pending."""
        outstanding_requests_num = len(
            self.tls_certificates.get_outstanding_certificate_requests()
        )
        total_requests_num = len(self.tls_certificates.get_certificate_requests())
        fulfilled_certs = total_requests_num - outstanding_requests_num
        return f"{fulfilled_certs}/{total_requests_num} certificate requests are fulfilled"

    def _is_relation_created(self, relation_name: str) -> bool:
        """Check if the relation is created.

        Args:
            relation_name: Checked relation name
        """
        return bool(self.model.relations.get(relation_name, []))

    @property
    def _cmd(self) -> List[str]:
        """Command to run to get the certificate.

        Returns:
            list[str]: Command and args to run.
        """
        if not self._email:
            raise ValueError("Email address was not provided")
        if not self._server:
            raise ValueError("ACME server was not provided")
        return [
            "lego",
            "--email",
            self._email,
            "--accept-tos",
            "--csr",
            self._csr_path,
            "--server",
            self._server,
            "--dns",
            self._plugin,
            "run",
        ]

    @property
    def _app_environment(self) -> Dict[str, str]:
        """Extract proxy model environment variables."""
        env = {}

        if http_proxy := get_env_var(env_var="JUJU_CHARM_HTTP_PROXY"):
            env["HTTP_PROXY"] = http_proxy
        if https_proxy := get_env_var(env_var="JUJU_CHARM_HTTPS_PROXY"):
            env["HTTPS_PROXY"] = https_proxy
        if no_proxy := get_env_var(env_var="JUJU_CHARM_NO_PROXY"):
            env["NO_PROXY"] = no_proxy
        return env

    @property
    @abstractmethod
    def _plugin_config(self) -> Dict[str, str]:
        """Plugin specific additional configuration for the command.

        Implement this method in your charm to return a dictionary with the plugin specific
        configuration.

        Returns:
            dict[str, str]: Plugin specific configuration.
        """

    @staticmethod
    def _email_is_valid(email: str) -> bool:
        """Validate the format of the email address."""
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return False
        return True

    @staticmethod
    def _server_is_valid(server: str) -> bool:
        """Validate the format of the ACME server address."""
        urlparts = urlparse(server)
        if not all([urlparts.scheme, urlparts.netloc]):
            return False
        return True

    @property
    def _email(self) -> Optional[str]:
        """Email address to use for the ACME account."""
        email = self.model.config.get("email", None)
        if not isinstance(email, str):
            return None
        return email

    @property
    def _server(self) -> Optional[str]:
        """ACME server address."""
        server = self.model.config.get("server", None)
        if not isinstance(server, str):
            return None
        return server


def get_env_var(env_var: str) -> Optional[str]:
    """Get the environment variable value.

    Looks for all upper-case and all low-case of the `env_var`.

    Args:
        env_var: Name of the environment variable.

    Returns:
        Value of the environment variable. None if not found.
    """
    return os.environ.get(env_var.upper(), os.environ.get(env_var.lower(), None))
