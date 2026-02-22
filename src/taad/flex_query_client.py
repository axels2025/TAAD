"""IBKR Flex Query Web Service client.

Two-step process:
1. Request a report via SendRequest — returns a reference code.
2. Poll GetStatement with that code until the XML is ready.

IBKR docs: https://www.interactivebrokers.com/en/software/am/am/reports/flexqueries.htm
"""

import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests
from loguru import logger

# IBKR Flex Query endpoints
FLEX_REQUEST_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
FLEX_STATEMENT_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"

# Polling configuration
DEFAULT_MAX_WAIT_SECONDS = 120
DEFAULT_POLL_INTERVAL_SECONDS = 5


class FlexQueryError(Exception):
    """Base exception for Flex Query operations."""
    pass


class FlexQueryClient:
    """Client for the IBKR Flex Query Web Service.

    Args:
        token: Flex Web Service token (from IBKR Account Management).
        query_id: Flex Query ID to execute.
        max_wait: Maximum seconds to wait for report generation.
        poll_interval: Seconds between status polls.
    """

    def __init__(
        self,
        token: str,
        query_id: str,
        max_wait: int = DEFAULT_MAX_WAIT_SECONDS,
        poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.token = token
        self.query_id = query_id
        self.max_wait = max_wait
        self.poll_interval = poll_interval

    def fetch_report(self) -> str:
        """Fetch the Flex Query report XML.

        Returns:
            Raw XML string from the Flex Query response.

        Raises:
            FlexQueryError: If the request fails or times out.
        """
        reference_code = self._send_request()
        xml_text = self._poll_statement(reference_code)
        return xml_text

    def _send_request(self) -> str:
        """Step 1: Send the Flex Query request and get a reference code.

        Returns:
            Reference code string for polling.

        Raises:
            FlexQueryError: If IBKR rejects the request.
        """
        params = {"t": self.token, "q": self.query_id, "v": "3"}

        logger.info(f"Requesting Flex Query {self.query_id}...")
        try:
            resp = requests.get(FLEX_REQUEST_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FlexQueryError(f"HTTP error requesting Flex Query: {e}") from e

        # Parse the response XML — use utf-8-sig to strip BOM
        try:
            send_text = resp.content.decode("utf-8-sig").strip()
        except (UnicodeDecodeError, AttributeError):
            send_text = resp.text.strip()

        try:
            root = ET.fromstring(send_text)
        except ET.ParseError as e:
            raise FlexQueryError(
                f"Failed to parse SendRequest response: {e}\nBody: {send_text[:500]}"
            ) from e

        status = root.findtext("Status", "")
        if status == "Success":
            ref_code = root.findtext("ReferenceCode", "")
            if not ref_code:
                raise FlexQueryError("SendRequest succeeded but no ReferenceCode returned")
            logger.info(f"Flex Query accepted, reference code: {ref_code}")
            return ref_code

        # Error path
        error_code = root.findtext("ErrorCode", "unknown")
        error_msg = root.findtext("ErrorMessage", resp.text[:200])
        raise FlexQueryError(
            f"Flex Query request rejected: [{error_code}] {error_msg}"
        )

    def _poll_statement(self, reference_code: str) -> str:
        """Step 2: Poll until the report is ready, then return the XML.

        Args:
            reference_code: Reference code from SendRequest.

        Returns:
            Raw XML string of the Flex Query report.

        Raises:
            FlexQueryError: If polling times out or fails.
        """
        params = {"t": self.token, "q": reference_code, "v": "3"}
        deadline = time.monotonic() + self.max_wait

        while time.monotonic() < deadline:
            logger.debug(f"Polling for statement {reference_code}...")
            try:
                resp = requests.get(FLEX_STATEMENT_URL, params=params, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                raise FlexQueryError(f"HTTP error polling statement: {e}") from e

            # Decode response — try content bytes first for proper encoding
            # IBKR sometimes returns UTF-8 with BOM or mismatched charset
            try:
                text = resp.content.decode("utf-8-sig").strip()
            except (UnicodeDecodeError, AttributeError):
                text = resp.text.strip()

            # Try to parse XML
            try:
                root = ET.fromstring(text)
            except ET.ParseError as parse_err:
                # Log detailed info for debugging
                logger.error(
                    f"XML parse error: {parse_err}\n"
                    f"Response length: {len(text)}\n"
                    f"First 500 chars: {text[:500]!r}"
                )
                raise FlexQueryError(
                    f"Unparseable response while polling: {text[:500]}"
                ) from parse_err

            # Distinguish full report from status envelope:
            # Full report has <FlexStatements> child, status envelope has <Status> child
            status = root.findtext("Status", "")

            if status == "Success":
                logger.info("Flex Query report received successfully")
                return text

            if status in ("Warn", "Fail"):
                error_code = root.findtext("ErrorCode", "")
                if error_code == "1019":
                    # Report not ready yet
                    logger.debug("Report generating, waiting...")
                    time.sleep(self.poll_interval)
                    continue
                error_msg = root.findtext("ErrorMessage", text[:200])
                raise FlexQueryError(
                    f"Unexpected status polling statement: [{error_code}] {error_msg}"
                )

            # No <Status> element — this is the full report XML
            if root.find(".//FlexStatements") is not None or root.find(".//TradeConfirm") is not None or root.find(".//Trade") is not None:
                logger.info("Flex Query report received successfully")
                return text

            # Unknown format
            raise FlexQueryError(
                f"Unrecognized response format: {text[:300]}"
            )

        raise FlexQueryError(
            f"Timed out waiting for Flex Query report after {self.max_wait}s"
        )


def fetch_flex_report(token: str, query_id: str) -> str:
    """Convenience function to fetch a Flex Query report.

    Args:
        token: Flex Web Service token.
        query_id: Flex Query ID.

    Returns:
        Raw XML string.
    """
    client = FlexQueryClient(token=token, query_id=query_id)
    return client.fetch_report()
