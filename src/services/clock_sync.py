"""Clock synchronization verification for FINRA compliance.

FINRA Rule 4590 requires that computer clocks used for recording order events
be synchronized to within 50 milliseconds of the NIST atomic clock.

This module provides NTP-based clock synchronization verification to ensure
regulatory compliance before executing trades.

Reference:
    FINRA Notice 14-47: https://www.finra.org/rules-guidance/notices/14-47

Usage:
    verifier = ClockSyncVerifier()
    is_synced, drift_ms = await verifier.verify_sync()

    if not is_synced:
        raise ClockSyncError(f"Clock drift {drift_ms:.1f}ms exceeds 50ms limit")
"""

import asyncio
from dataclasses import dataclass

import ntplib
from loguru import logger


class ClockSyncError(Exception):
    """Raised when system clock drift exceeds regulatory limits."""
    pass


@dataclass
class ClockSyncResult:
    """Result of clock synchronization check.

    Attributes:
        is_synced: True if drift within threshold
        drift_ms: Clock drift in milliseconds
        threshold_ms: Maximum allowed drift (default: 50ms)
        ntp_server: NTP server used for verification
    """
    is_synced: bool
    drift_ms: float
    threshold_ms: float
    ntp_server: str

    def __str__(self) -> str:
        """Format sync result for logging."""
        status = "✓ SYNCED" if self.is_synced else "✗ OUT OF SYNC"
        return (
            f"Clock Sync: {status}\n"
            f"  Drift: {self.drift_ms:.1f}ms\n"
            f"  Threshold: {self.threshold_ms:.1f}ms\n"
            f"  Server: {self.ntp_server}"
        )


class ClockSyncVerifier:
    """Verify system clock synchronization against NTP servers.

    FINRA requires that trading system clocks be synchronized to within
    50 milliseconds of NIST atomic time. This class provides verification
    before executing trades.

    The verifier queries NIST NTP servers and measures the offset between
    system time and atomic time. If drift exceeds threshold, execution
    should be aborted.

    Example:
        verifier = ClockSyncVerifier(threshold_ms=50.0)

        try:
            result = await verifier.verify_sync()
            if not result.is_synced:
                raise ClockSyncError(
                    f"Clock drift {result.drift_ms:.1f}ms exceeds limit"
                )

            logger.info(f"✓ Clock synced: {result.drift_ms:.1f}ms drift")

        except ClockSyncError as e:
            logger.error(f"Aborting execution: {e}")
            return
    """

    # NIST NTP servers (primary and fallbacks)
    NTP_SERVERS = [
        "time.nist.gov",      # NIST primary
        "time-a-g.nist.gov",  # NIST alternate
        "pool.ntp.org",       # NTP pool (fallback)
    ]

    def __init__(self, threshold_ms: float = 50.0):
        """Initialize clock sync verifier.

        Args:
            threshold_ms: Maximum allowed drift in milliseconds (default: 50ms for FINRA)
        """
        self.threshold_ms = threshold_ms
        self.ntp_client = ntplib.NTPClient()

        logger.info(
            f"ClockSyncVerifier initialized: {self.threshold_ms:.1f}ms threshold"
        )

    async def verify_sync(
        self,
        timeout: float = 5.0
    ) -> ClockSyncResult:
        """Verify system clock within threshold of NTP time.

        Queries NIST NTP servers to measure clock drift. Tries primary server
        first, falls back to alternates if unavailable.

        Args:
            timeout: NTP query timeout in seconds (default: 5.0)

        Returns:
            ClockSyncResult with drift measurement and sync status

        Raises:
            ClockSyncError: If all NTP servers fail or timeout

        Example:
            result = await verifier.verify_sync()

            if result.is_synced:
                print(f"✓ Synced: {result.drift_ms:.1f}ms drift")
            else:
                print(f"✗ Out of sync: {result.drift_ms:.1f}ms drift")
        """
        for ntp_server in self.NTP_SERVERS:
            try:
                # Run NTP query in thread pool (ntplib is synchronous)
                response = await asyncio.to_thread(
                    self._query_ntp_server,
                    ntp_server,
                    timeout
                )

                # Calculate drift in milliseconds
                drift_ms = abs(response.offset) * 1000

                # Check if within threshold
                is_synced = drift_ms <= self.threshold_ms

                result = ClockSyncResult(
                    is_synced=is_synced,
                    drift_ms=drift_ms,
                    threshold_ms=self.threshold_ms,
                    ntp_server=ntp_server
                )

                if is_synced:
                    logger.info(
                        f"✓ Clock synced: {drift_ms:.1f}ms drift "
                        f"(server: {ntp_server})"
                    )
                else:
                    logger.error(
                        f"✗ Clock drift {drift_ms:.1f}ms exceeds "
                        f"{self.threshold_ms:.1f}ms limit (server: {ntp_server})"
                    )

                return result

            except Exception as e:
                logger.warning(f"NTP query failed for {ntp_server}: {e}")
                continue  # Try next server

        # All servers failed
        raise ClockSyncError(
            f"All NTP servers failed - cannot verify clock sync. "
            f"Tried: {', '.join(self.NTP_SERVERS)}"
        )

    def _query_ntp_server(self, server: str, timeout: float) -> ntplib.NTPStats:
        """Query NTP server (synchronous, runs in thread pool).

        Args:
            server: NTP server hostname
            timeout: Query timeout in seconds

        Returns:
            NTP response with offset measurement

        Raises:
            ntplib.NTPException: If query fails
        """
        return self.ntp_client.request(server, version=3, timeout=timeout)

    async def verify_sync_or_abort(self) -> float:
        """Verify clock sync and abort if out of sync.

        Convenience method that verifies sync and raises ClockSyncError
        if drift exceeds threshold. Use this at the start of execution
        workflows.

        Returns:
            Clock drift in milliseconds (if synced)

        Raises:
            ClockSyncError: If drift exceeds threshold or verification fails

        Example:
            verifier = ClockSyncVerifier()

            # At start of execution workflow
            try:
                drift_ms = await verifier.verify_sync_or_abort()
                logger.info(f"✓ Clock synced: {drift_ms:.1f}ms drift - proceeding")

            except ClockSyncError as e:
                logger.error(f"✗ Aborting execution: {e}")
                return ExecutionReport(aborted=True, reason=str(e))
        """
        result = await self.verify_sync()

        if not result.is_synced:
            raise ClockSyncError(
                f"Clock drift {result.drift_ms:.1f}ms exceeds "
                f"{result.threshold_ms:.1f}ms FINRA limit - aborting execution"
            )

        return result.drift_ms
