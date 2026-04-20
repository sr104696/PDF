"""
rate_limiter.py - Token Bucket and Exponential Backoff Rate Limiting
Implements efficient rate limiting for PRAW (Reddit API) and LLM API calls.
"""
import time
import threading
from typing import Optional, Callable, Any
from functools import wraps
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TokenBucket:
    """
    Token bucket rate limiter for controlling API request rates.
    
    Tokens are added at a constant rate up to a maximum capacity.
    Each request consumes one token. If no tokens are available,
    the request waits until a token becomes available.
    """
    
    def __init__(self, capacity: float = 60.0, refill_rate: float = 1.0):
        """
        Initialize the token bucket.
        
        Args:
            capacity: Maximum number of tokens in the bucket (max requests per window)
            refill_rate: Number of tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.time()
        self._lock = threading.Lock()
    
    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now
    
    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire a token from the bucket.
        
        Args:
            blocking: If True, wait for a token. If False, return immediately.
            timeout: Maximum time to wait for a token (None = wait indefinitely)
        
        Returns:
            True if token acquired, False if timeout or non-blocking and no tokens
        """
        start_time = time.time()
        
        while True:
            with self._lock:
                self._refill()
                
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                
                if not blocking:
                    return False
                
                # Calculate wait time for next token
                tokens_needed = 1.0 - self.tokens
                wait_time = tokens_needed / self.refill_rate
            
            # Check timeout
            if timeout is not None:
                elapsed = time.time() - start_time
                remaining = timeout - elapsed
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)
            
            # Wait for tokens to refill
            time.sleep(min(wait_time, 0.1))  # Sleep in small increments
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to rate limit a function."""
        @wraps(func)
        def wrapper(*args, **kwargs):
            self.acquire()
            return func(*args, **kwargs)
        return wrapper


class ExponentialBackoff:
    """
    Exponential backoff retry handler for API calls.
    
    Retries failed requests with exponentially increasing delays.
    Useful for handling rate limits and transient errors.
    """
    
    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 300.0,
        multiplier: float = 2.0,
        max_retries: int = 5,
        jitter: bool = True
    ):
        """
        Initialize exponential backoff handler.
        
        Args:
            initial_delay: Initial delay in seconds
            max_delay: Maximum delay in seconds
            multiplier: Delay multiplier for each retry
            max_retries: Maximum number of retry attempts
            jitter: Add random jitter to prevent thundering herd
        """
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.max_retries = max_retries
        self.jitter = jitter
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        import random
        
        delay = self.initial_delay * (self.multiplier ** attempt)
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            # Add up to 25% jitter
            delay = delay * (0.75 + random.random() * 0.5)
        
        return delay
    
    def execute(
        self,
        func: Callable,
        *args,
        retryable_exceptions: tuple = (Exception,),
        **kwargs
    ) -> Any:
        """
        Execute a function with exponential backoff retry logic.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            retryable_exceptions: Tuple of exceptions that trigger retries
            **kwargs: Keyword arguments for the function
        
        Returns:
            Result of the function call
        
        Raises:
            The last exception if all retries fail
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except retryable_exceptions as e:
                last_exception = e
                
                if attempt < self.max_retries:
                    delay = self.get_delay(attempt)
                    logger.warning(
                        f"Attempt {attempt + 1}/{self.max_retries + 1} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"All {self.max_retries + 1} attempts failed. "
                        f"Last error: {e}"
                    )
        
        raise last_exception
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to add exponential backoff retry logic to a function."""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.execute(func, *args, **kwargs)
        return wrapper


class RateLimitedPRAW:
    """
    Rate-limited wrapper for PRAW Reddit API calls.
    
    Combines token bucket rate limiting with exponential backoff
    for robust API access.
    """
    
    def __init__(
        self,
        reddit_client,
        bucket_capacity: float = 60.0,
        bucket_refill_rate: float = 1.0,
        backoff_initial_delay: float = 1.0,
        backoff_max_delay: float = 300.0,
        backoff_multiplier: float = 2.0,
        backoff_max_retries: int = 5
    ):
        """
        Initialize rate-limited PRAW wrapper.
        
        Args:
            reddit_client: PRAW Reddit instance
            bucket_capacity: Token bucket capacity (requests per minute)
            bucket_refill_rate: Token bucket refill rate (tokens/second)
            backoff_*: Exponential backoff parameters
        """
        self.reddit = reddit_client
        self.bucket = TokenBucket(capacity=bucket_capacity, refill_rate=bucket_refill_rate)
        self.backoff = ExponentialBackoff(
            initial_delay=backoff_initial_delay,
            max_delay=backoff_max_delay,
            multiplier=backoff_multiplier,
            max_retries=backoff_max_retries
        )
    
    def _rate_limited_call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a PRAW call with rate limiting and backoff."""
        # First, acquire a token from the bucket
        self.bucket.acquire()
        
        # Then execute with exponential backoff for errors
        def wrapped_call():
            return func(*args, **kwargs)
        
        return self.backoff.execute(
            wrapped_call,
            retryable_exceptions=(Exception,)  # Customize based on PRAW exceptions
        )
    
    def subreddit(self, name: str):
        """Get a subreddit with rate limiting."""
        return self._rate_limited_call(self.reddit.subreddit, name)
    
    def redditor(self, name: str):
        """Get a redditor with rate limiting."""
        return self._rate_limited_call(self.reddit.redditor, name)
    
    def info(self, fullname: str):
        """Get submission info with rate limiting."""
        return self._rate_limited_call(self.reddit.info, fullname)
    
    def inbox(self):
        """Get inbox with rate limiting."""
        return self._rate_limited_call(lambda: self.reddit.inbox.all())


def rate_limit(
    capacity: float = 60.0,
    refill_rate: float = 1.0,
    backoff_initial_delay: float = 1.0,
    backoff_max_delay: float = 300.0,
    backoff_multiplier: float = 2.0,
    backoff_max_retries: int = 5
):
    """
    Decorator factory for rate limiting functions.
    
    Args:
        capacity: Token bucket capacity
        refill_rate: Token bucket refill rate
        backoff_*: Exponential backoff parameters
    
    Returns:
        Decorator function
    """
    bucket = TokenBucket(capacity=capacity, refill_rate=refill_rate)
    backoff = ExponentialBackoff(
        initial_delay=backoff_initial_delay,
        max_delay=backoff_max_delay,
        multiplier=backoff_multiplier,
        max_retries=backoff_max_retries
    )
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            bucket.acquire()
            
            def wrapped_call():
                return func(*args, **kwargs)
            
            return backoff.execute(wrapped_call)
        
        return wrapper
    
    return decorator


# Example usage
if __name__ == "__main__":
    # Example: Rate limit a function to 10 calls per second
    @rate_limit(capacity=10, refill_rate=10)
    def api_call():
        print("API call executed")
        return "success"
    
    # Test the rate limiter
    for i in range(15):
        result = api_call()
        print(f"Call {i+1}: {result}")
