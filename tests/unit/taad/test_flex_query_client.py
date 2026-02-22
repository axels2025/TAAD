"""Tests for TAAD Flex Query API client."""

import pytest
from unittest.mock import MagicMock, patch

from src.taad.flex_query_client import FlexQueryClient, FlexQueryError


MOCK_SEND_RESPONSE_SUCCESS = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp=''>
<Status>Success</Status>
<ReferenceCode>1234567890</ReferenceCode>
<Url>https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement</Url>
</FlexStatementResponse>"""

MOCK_SEND_RESPONSE_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp=''>
<Status>Fail</Status>
<ErrorCode>1003</ErrorCode>
<ErrorMessage>Invalid token</ErrorMessage>
</FlexStatementResponse>"""

MOCK_POLL_NOT_READY = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse timestamp=''>
<Status>Warn</Status>
<ErrorCode>1019</ErrorCode>
<ErrorMessage>Statement generation in progress. Please try again shortly.</ErrorMessage>
</FlexStatementResponse>"""

MOCK_POLL_SUCCESS = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse>
<FlexStatements count="1">
<FlexStatement accountId="YOUR_ACCOUNT">
<TradeConfirms>
<TradeConfirm accountId="YOUR_ACCOUNT" symbol="AAPL" buySell="SELL" quantity="-5" />
</TradeConfirms>
</FlexStatement>
</FlexStatements>
</FlexStatementResponse>"""


class TestFlexQueryClient:
    def test_init(self):
        client = FlexQueryClient(token="test-token", query_id="12345")
        assert client.token == "test-token"
        assert client.query_id == "12345"

    @patch("src.taad.flex_query_client.requests.get")
    def test_send_request_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = MOCK_SEND_RESPONSE_SUCCESS
        mock_resp.content = MOCK_SEND_RESPONSE_SUCCESS.encode("utf-8")
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = FlexQueryClient(token="test-token", query_id="12345")
        ref_code = client._send_request()

        assert ref_code == "1234567890"
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["t"] == "test-token"
        assert call_kwargs[1]["params"]["q"] == "12345"

    @patch("src.taad.flex_query_client.requests.get")
    def test_send_request_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = MOCK_SEND_RESPONSE_ERROR
        mock_resp.content = MOCK_SEND_RESPONSE_ERROR.encode("utf-8")
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = FlexQueryClient(token="bad-token", query_id="12345")
        with pytest.raises(FlexQueryError, match="Invalid token"):
            client._send_request()

    @patch("src.taad.flex_query_client.requests.get")
    def test_poll_statement_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = MOCK_POLL_SUCCESS
        mock_resp.content = MOCK_POLL_SUCCESS.encode("utf-8")
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client = FlexQueryClient(token="test-token", query_id="12345")
        result = client._poll_statement("1234567890")

        assert "<TradeConfirm" in result

    @patch("src.taad.flex_query_client.time.monotonic")
    @patch("src.taad.flex_query_client.time.sleep")
    @patch("src.taad.flex_query_client.requests.get")
    def test_poll_retries_on_not_ready(self, mock_get, mock_sleep, mock_monotonic):
        """Should retry when IBKR says statement generation in progress."""
        # First call: not ready, second call: success
        not_ready_resp = MagicMock()
        not_ready_resp.text = MOCK_POLL_NOT_READY
        not_ready_resp.content = MOCK_POLL_NOT_READY.encode("utf-8")
        not_ready_resp.raise_for_status = MagicMock()

        ready_resp = MagicMock()
        ready_resp.text = MOCK_POLL_SUCCESS
        ready_resp.content = MOCK_POLL_SUCCESS.encode("utf-8")
        ready_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [not_ready_resp, ready_resp]

        # Simulate time: first check at 0s, deadline at 120s
        mock_monotonic.side_effect = [0.0, 5.0, 10.0]

        client = FlexQueryClient(token="test-token", query_id="12345", max_wait=120)
        result = client._poll_statement("1234567890")

        assert "<TradeConfirm" in result
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(5)

    @patch("src.taad.flex_query_client.time.monotonic")
    @patch("src.taad.flex_query_client.requests.get")
    def test_poll_timeout(self, mock_get, mock_monotonic):
        """Should raise error when polling times out."""
        not_ready_resp = MagicMock()
        not_ready_resp.text = MOCK_POLL_NOT_READY
        not_ready_resp.content = MOCK_POLL_NOT_READY.encode("utf-8")
        not_ready_resp.raise_for_status = MagicMock()
        mock_get.return_value = not_ready_resp

        # First call sets deadline = 0.0 + 10 = 10.0
        # Second call (while check) returns 999.0 > 10.0 → loop exits
        mock_monotonic.side_effect = [0.0, 999.0]

        client = FlexQueryClient(token="test-token", query_id="12345", max_wait=10)
        with pytest.raises(FlexQueryError, match="Timed out"):
            client._poll_statement("1234567890")

    @patch("src.taad.flex_query_client.requests.get")
    def test_fetch_report_end_to_end(self, mock_get):
        """Full fetch_report flow: send request → poll → return XML."""
        send_resp = MagicMock()
        send_resp.text = MOCK_SEND_RESPONSE_SUCCESS
        send_resp.content = MOCK_SEND_RESPONSE_SUCCESS.encode("utf-8")
        send_resp.raise_for_status = MagicMock()

        poll_resp = MagicMock()
        poll_resp.text = MOCK_POLL_SUCCESS
        poll_resp.content = MOCK_POLL_SUCCESS.encode("utf-8")
        poll_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [send_resp, poll_resp]

        client = FlexQueryClient(token="test-token", query_id="12345")
        result = client.fetch_report()

        assert "<TradeConfirm" in result
        assert mock_get.call_count == 2

    @patch("src.taad.flex_query_client.requests.get")
    def test_http_error(self, mock_get):
        """Should wrap HTTP errors in FlexQueryError."""
        from requests.exceptions import ConnectionError

        mock_get.side_effect = ConnectionError("Connection refused")

        client = FlexQueryClient(token="test-token", query_id="12345")
        with pytest.raises(FlexQueryError, match="HTTP error"):
            client._send_request()
