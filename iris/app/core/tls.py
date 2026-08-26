"""Make outbound HTTPS trust the operating system's certificate store.

Python ships its own CA bundle (certifi), which does not contain the roots
installed by antivirus suites (Avast, Kaspersky, Bitdefender...), school or
corporate TLS-inspection proxies. On such machines every provider call fails
with CERTIFICATE_VERIFY_FAILED while the browser works fine — the single most
confusing failure mode IRIS users hit on Windows.

``truststore`` (the same approach pip uses) swaps Python's default SSL context
for one backed by the OS store, so IRIS trusts exactly what the browser
trusts. Never disable verification; this keeps it on while fixing the trust
source.
"""

from __future__ import annotations

from iris.app.core.logging import get_logger

logger = get_logger("core.tls")

_injected = False


def use_system_trust_store() -> bool:
    """Route SSL verification through the OS trust store. Idempotent.

    Returns True when active. Failure is non-fatal: without truststore
    installed, Python's bundled CA list is used as before.
    """
    global _injected
    if _injected:
        return True
    try:
        import truststore

        truststore.inject_into_ssl()
        _injected = True
        logger.debug("System certificate trust store active.")
        return True
    except ImportError:
        logger.debug("truststore not installed; using Python's bundled CA list.")
        return False
    except Exception as exc:  # noqa: BLE001 - never let TLS setup kill startup
        logger.warning("Could not enable the system trust store: %s", exc)
        return False
