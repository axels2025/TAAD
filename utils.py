"""
Utility functions for error handling, retries, and common operations
"""
import logging
import time
from functools import wraps
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)


class RetryException(Exception):
    """Exception raised when all retry attempts fail"""
    pass


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying functions with exponential backoff

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay after each attempt
        exceptions: Tuple of exceptions to catch and retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise RetryException(
                            f"Failed after {max_attempts} attempts: {e}"
                        ) from e

                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    time.sleep(delay)
                    delay *= backoff_factor

            # Should never reach here, but just in case
            raise RetryException(f"Unexpected failure in {func.__name__}")

        return wrapper
    return decorator


def safe_execute(
    func: Callable,
    default_return: Any = None,
    log_errors: bool = True
) -> Any:
    """
    Safely execute a function and return default value on error

    Args:
        func: Function to execute
        default_return: Value to return on error
        log_errors: Whether to log errors

    Returns:
        Function result or default value
    """
    try:
        return func()
    except Exception as e:
        if log_errors:
            logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
        return default_return


def validate_price(price: float, min_price: float = 0.01) -> bool:
    """
    Validate that a price is reasonable

    Args:
        price: Price to validate
        min_price: Minimum acceptable price

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(price, (int, float)):
        return False

    if price < min_price:
        return False

    if price > 100000:  # Sanity check
        return False

    return True


def validate_percentage(pct: float, min_pct: float = 0.0, max_pct: float = 100.0) -> bool:
    """
    Validate that a percentage is in range

    Args:
        pct: Percentage to validate
        min_pct: Minimum acceptable percentage
        max_pct: Maximum acceptable percentage

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(pct, (int, float)):
        return False

    return min_pct <= pct <= max_pct


def format_currency(amount: float) -> str:
    """Format amount as currency"""
    return f"${amount:,.2f}"


def format_percentage(pct: float) -> str:
    """Format percentage"""
    return f"{pct:.2f}%"


class RateLimiter:
    """Simple rate limiter for API calls"""

    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0

    def wait_if_needed(self):
        """Wait if needed to respect rate limit"""
        now = time.time()
        elapsed = now - self.last_call

        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f}s")
            time.sleep(sleep_time)

        self.last_call = time.time()


class CircuitBreaker:
    """
    Circuit breaker pattern for failing operations
    Prevents repeated attempts to failing operations
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call function through circuit breaker

        Args:
            func: Function to call
            *args, **kwargs: Arguments to pass to function

        Returns:
            Function result

        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        # Check if circuit should transition from open to half-open
        if self.state == "open":
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                logger.info("Circuit breaker transitioning to half-open")
                self.state = "half-open"
            else:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker is open. "
                    f"Will retry after {self.recovery_timeout}s"
                )

        try:
            result = func(*args, **kwargs)

            # Success - reset circuit
            if self.state == "half-open":
                logger.info("Circuit breaker closing after successful call")
                self.state = "closed"
                self.failure_count = 0

            return result

        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()

            logger.warning(
                f"Circuit breaker recorded failure {self.failure_count}/"
                f"{self.failure_threshold}: {e}"
            )

            if self.failure_count >= self.failure_threshold:
                logger.error("Circuit breaker opening due to failures")
                self.state = "open"

            raise


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


if __name__ == "__main__":
    # Test retry decorator
    logging.basicConfig(level=logging.INFO)

    @retry_with_backoff(max_attempts=3, initial_delay=0.5)
    def flaky_function():
        import random
        if random.random() < 0.7:
            raise ValueError("Random failure")
        return "Success!"

    try:
        result = flaky_function()
        print(f"Result: {result}")
    except RetryException as e:
        print(f"Failed: {e}")
