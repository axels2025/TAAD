"""Base agent with reusable Claude API wrapper.

Provides client initialization, model selection, retry logic,
and cost tracking for all AI agent interactions.

A module-level threading lock serialises all Claude API calls so that
concurrent callers (e.g. reasoning + CRO running in separate
asyncio.to_thread workers) do not fire duplicate requests during
credit exhaustion or rate-limit back-off.
"""

import threading
import time
from typing import Optional

from anthropic import Anthropic, APIError, APITimeoutError, BadRequestError, RateLimitError
from loguru import logger

from src.config.base import get_config


# Serialise all Claude API calls across agent instances.
# This prevents duplicate concurrent requests when credits are
# exhausted or the API is rate-limiting — each caller waits its
# turn instead of independently retrying in parallel.
_api_lock = threading.Lock()

# Circuit breaker: when a credit/auth error is hit, block all calls
# for this many seconds to avoid hammering the API.
_circuit_breaker_until: float = 0.0
_CIRCUIT_BREAKER_COOLDOWN = 60.0  # seconds

# Approximate pricing per 1M tokens (as of 2026-02)
_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}


class BaseAgent:
    """Reusable Claude API wrapper with retries and cost tracking.

    Handles client initialization, model selection, retries on transient
    errors, and token/cost accounting.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        max_retries: int = 3,
        timeout: float = 60.0,
        api_key: Optional[str] = None,
    ):
        """Initialize the base agent.

        Args:
            model: Claude model ID to use
            max_retries: Maximum retry attempts on transient failures
            timeout: Request timeout in seconds
            api_key: Optional API key override (uses Config if not provided)
        """
        if api_key is None:
            config = get_config()
            api_key = config.anthropic_api_key
        if not api_key:
            raise ValueError(
                "Anthropic API key required for AI agent features. "
                "Set ANTHROPIC_API_KEY in your .env file."
            )
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout

        # Cost tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_requests = 0

    def send_message(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> dict:
        """Send a message to Claude with retry logic.

        All calls are serialised through a module-level lock so that
        concurrent callers (reasoning, CRO, reflection) cannot fire
        duplicate requests during credit exhaustion or rate limiting.

        A circuit breaker trips on credit/auth errors (401/403) and
        blocks all callers for 60 seconds, preventing a storm of
        doomed retries.

        Args:
            system_prompt: System instructions for Claude
            user_message: The user/data message
            max_tokens: Maximum response tokens
            temperature: Sampling temperature (lower = more deterministic)

        Returns:
            Dict with 'content', 'input_tokens', 'output_tokens', 'model'

        Raises:
            APIError: After all retries exhausted
        """
        global _circuit_breaker_until

        # Check circuit breaker before acquiring the lock
        remaining = _circuit_breaker_until - time.monotonic()
        if remaining > 0:
            raise APIError(
                message=f"Circuit breaker open — API calls blocked for {remaining:.0f}s after credit/auth failure",
                request=None,
                body=None,
            )

        last_error = None

        with _api_lock:
            for attempt in range(1, self.max_retries + 1):
                # Re-check circuit breaker inside retry loop
                if _circuit_breaker_until > time.monotonic():
                    raise APIError(
                        message="Circuit breaker tripped during retry — aborting",
                        request=None,
                        body=None,
                    )

                try:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_message}],
                        timeout=self.timeout,
                    )

                    input_tokens = response.usage.input_tokens
                    output_tokens = response.usage.output_tokens
                    self.total_input_tokens += input_tokens
                    self.total_output_tokens += output_tokens
                    self.total_requests += 1

                    content = response.content[0].text if response.content else ""

                    cost = self.estimate_cost(input_tokens, output_tokens)
                    logger.info(
                        f"CLAUDE API CALL: model={self.model}, "
                        f"tokens={input_tokens}in/{output_tokens}out, "
                        f"cost=${cost:.4f}, "
                        f"session_total=${self.session_cost:.4f}"
                    )

                    return {
                        "content": content,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "model": self.model,
                    }

                except RateLimitError as e:
                    last_error = e
                    wait = min(2 ** attempt, 30)
                    logger.warning(f"Rate limited (attempt {attempt}/{self.max_retries}), waiting {wait}s")
                    time.sleep(wait)

                except APITimeoutError as e:
                    last_error = e
                    logger.warning(f"Timeout (attempt {attempt}/{self.max_retries})")

                except BadRequestError:
                    raise  # 400 errors are not retryable

                except APIError as e:
                    last_error = e
                    if e.status_code and e.status_code in (401, 403):
                        # Credit exhaustion or auth failure — trip circuit breaker
                        _circuit_breaker_until = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN
                        logger.error(
                            f"Credit/auth error ({e.status_code}), "
                            f"circuit breaker tripped for {_CIRCUIT_BREAKER_COOLDOWN:.0f}s"
                        )
                        raise
                    elif e.status_code and e.status_code >= 500:
                        wait = min(2 ** attempt, 30)
                        logger.warning(f"Server error {e.status_code} (attempt {attempt}/{self.max_retries}), waiting {wait}s")
                        time.sleep(wait)
                    else:
                        raise

        raise last_error  # type: ignore[misc]

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for a request in dollars.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Estimated cost in USD
        """
        pricing = _PRICING.get(self.model, {"input": 3.00, "output": 15.00})
        return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

    @property
    def session_cost(self) -> float:
        """Total estimated cost for this agent's session."""
        return self.estimate_cost(self.total_input_tokens, self.total_output_tokens)
