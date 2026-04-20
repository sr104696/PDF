"""
batch_api.py - Optimized for OpenAI Batch API with 50% cost savings
All LLM calls use Batch API only - no synchronous calls allowed.
Implements exponential backoff for rate limit handling.
"""
import json
import uuid
import time
import os
import openai
import requests
from typing import List, Dict, Optional, Tuple
from config.config_loader import get_config
from scheduler.cost_tracker import add_cost
from utils.logger import setup_logger

log = setup_logger()
config = get_config()

RESPONSE_DIR = "data/batch_responses"

# Rate limiting configuration
RATE_LIMIT_CONFIG = config.get("rate_limiting", {}).get("llm", {
    "initial_delay": 1.0,
    "max_delay": 300.0,
    "multiplier": 2.0,
    "max_retries": 5
})


class RateLimitExceeded(Exception):
    """Custom exception for rate limit errors."""
    pass


def exponential_backoff(
    func,
    *args,
    initial_delay: float = None,
    max_delay: float = None,
    multiplier: float = None,
    max_retries: int = None,
    **kwargs
):
    """
    Execute a function with exponential backoff on rate limit errors.
    
    Args:
        func: Function to execute
        *args: Positional arguments for the function
        initial_delay: Initial delay in seconds (default from config)
        max_delay: Maximum delay in seconds (default from config)
        multiplier: Backoff multiplier (default from config)
        max_retries: Maximum retry attempts (default from config)
        **kwargs: Keyword arguments for the function
    
    Returns:
        Result of the function call
    
    Raises:
        RateLimitExceeded: If all retries are exhausted
    """
    initial_delay = initial_delay or RATE_LIMIT_CONFIG["initial_delay"]
    max_delay = max_delay or RATE_LIMIT_CONFIG["max_delay"]
    multiplier = multiplier or RATE_LIMIT_CONFIG["multiplier"]
    max_retries = max_retries or RATE_LIMIT_CONFIG["max_retries"]
    
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except openai.RateLimitError as e:
            last_exception = e
            if attempt < max_retries:
                log.warning(f"Rate limit hit. Attempt {attempt + 1}/{max_retries}. "
                           f"Waiting {delay:.1f}s before retry...")
                time.sleep(delay)
                delay = min(delay * multiplier, max_delay)
            else:
                log.error(f"Rate limit exceeded after {max_retries} retries.")
        except openai.APIConnectionError as e:
            last_exception = e
            if attempt < max_retries:
                log.warning(f"API connection error. Attempt {attempt + 1}/{max_retries}. "
                           f"Waiting {delay:.1f}s before retry...")
                time.sleep(delay)
                delay = min(delay * multiplier, max_delay)
            else:
                log.error(f"API connection failed after {max_retries} retries.")
        except Exception as e:
            # Non-retryable errors
            log.error(f"Unexpected error: {e}")
            raise
    
    raise RateLimitExceeded(f"Failed after {max_retries} retries: {last_exception}")


def clean_storage():
    """Delete all batch-related files from OpenAI's file storage.

    OpenAI's storage accumulates input/output files from previous batch runs.
    If not cleaned, this can block new batch submissions due to storage limits.
    """
    def _clean():
        files = openai.files.list()
        deleted = 0
        for f in files.data:
            if f.purpose in ("batch", "batch_output"):
                try:
                    openai.files.delete(f.id)
                    deleted += 1
                except Exception as e:
                    log.warning(f"Failed to delete file {f.id}: {e}")
        if deleted:
            log.info(f"Cleaned {deleted} batch files from OpenAI storage.")
        else:
            log.info("No batch files found in OpenAI storage to clean.")
    
    return exponential_backoff(_clean)


def generate_batch_payload(requests: list[dict], model: str) -> str:
    """Create a JSONL file from prompts for OpenAI batch processing.
    
    This is the ONLY method for submitting LLM requests - no synchronous calls.
    """
    os.makedirs(RESPONSE_DIR, exist_ok=True)
    job_id = str(uuid.uuid4())
    path = f"{RESPONSE_DIR}/batch_{job_id}.jsonl"

    with open(path, "w", encoding="utf-8") as f:
        for prompt in requests:
            body = {
                    "model": model,
                    "messages": prompt["messages"],
                    "response_format": {"type": "json_object"},
                }
            # gpt-5+ models only support the default temperature (1)
            if not model.startswith("gpt-5"):
                body["temperature"] = 0
            f.write(json.dumps({
                "custom_id": prompt.get("id", str(uuid.uuid4())),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }) + "\n")

    log.info(f"Batch payload generated at: {path} with {len(requests)} entries")
    return path


def submit_batch_job(file_path: str, endpoint: str = "/v1/chat/completions",
                     estimated_tokens: int = 0) -> str:
    """Uploads file and submits a batch job to OpenAI.

    If estimated_tokens is provided, it's stored in batch metadata so we can
    query OpenAI later to compute real enqueued token counts.
    
    Uses exponential backoff for rate limit handling.
    """
    def _upload_and_submit():
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        log.info(f"Uploading batch file ({file_size_mb:.1f} MB)...")
        uploaded_file = openai.files.create(file=open(file_path, "rb"), purpose="batch")
        log.info(f"Uploaded file for batch: {uploaded_file.id}")

        metadata = {}
        if estimated_tokens:
            metadata["estimated_tokens"] = str(estimated_tokens)

        batch = openai.batches.create(
            input_file_id=uploaded_file.id,
            endpoint=endpoint,
            completion_window="24h",
            metadata=metadata if metadata else None,
        )
        log.info(f"Submitted batch job: {batch.id}")
        return batch.id
    
    return exponential_backoff(_upload_and_submit)


def get_active_enqueued_tokens() -> int:
    """Query OpenAI for all active batches and sum their estimated enqueued tokens.

    Uses batch metadata (estimated_tokens) when available, otherwise falls back
    to estimating from pending request counts.
    """
    def _query():
        active_statuses = {"validating", "in_progress", "finalizing"}
        total_tokens = 0
        avg_tokens_per_request = 350  # fallback estimate

        batches = openai.batches.list(limit=100)
        for batch in batches.data:
            if batch.status not in active_statuses:
                continue

            # Prefer metadata estimate (set by us at submit time)
            metadata_tokens = (batch.metadata or {}).get("estimated_tokens")
            if metadata_tokens:
                batch_total = int(metadata_tokens)
                # Subtract completed portion
                counts = batch.request_counts
                total_reqs = getattr(counts, "total", 0)
                completed_reqs = getattr(counts, "completed", 0)
                if total_reqs > 0:
                    remaining_ratio = (total_reqs - completed_reqs) / total_reqs
                    total_tokens += int(batch_total * remaining_ratio)
                else:
                    total_tokens += batch_total
            else:
                # Fallback: estimate from pending request count
                counts = batch.request_counts
                total_reqs = getattr(counts, "total", 0)
                completed_reqs = getattr(counts, "completed", 0)
                pending = total_reqs - completed_reqs
                total_tokens += pending * avg_tokens_per_request

        log.info(f"Active enqueued tokens at OpenAI: {total_tokens:,}")
        return total_tokens
    
    try:
        return exponential_backoff(_query)
    except Exception as e:
        log.warning(f"Failed to query active batches from OpenAI: {e}")
        return 0


def probe_enqueued_capacity(model: str, max_wait=7200, poll_interval=300) -> bool:
    """Submit a tiny 1-request probe batch to test if the enqueued token limit is free.

    OpenAI has a known bug where failed batches' tokens aren't released from the
    enqueued quota. Our API queries show 0 active batches, but submitting still
    fails with 'token_limit_exceeded'. The only way to know for sure is to try.

    This function submits a minimal probe and waits for it to either succeed
    (meaning capacity is available) or fail (meaning ghost tokens are still held).
    If it fails, it retries with exponential backoff up to max_wait seconds.

    Returns True if capacity is confirmed, False if still blocked after max_wait.
    """
    os.makedirs(RESPONSE_DIR, exist_ok=True)

    # Build a minimal 1-request probe batch
    probe_request = {
        "custom_id": "probe-test",
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_completion_tokens": 8,
        }
    }
    probe_path = f"{RESPONSE_DIR}/probe_{uuid.uuid4().hex}.jsonl"
    with open(probe_path, "w") as f:
        f.write(json.dumps(probe_request) + "\n")

    start_time = time.time()
    delay = poll_interval
    attempt = 0

    while time.time() - start_time < max_wait:
        attempt += 1
        elapsed = int(time.time() - start_time)
        log.info(f"[Probe attempt {attempt}] Testing enqueued capacity for {model}... "
                 f"(elapsed: {elapsed}s / {max_wait}s)")
        try:
            def _submit_probe():
                uploaded = openai.files.create(file=open(probe_path, "rb"), purpose="batch")
                batch = openai.batches.create(
                    input_file_id=uploaded.id,
                    endpoint="/v1/chat/completions",
                    completion_window="24h",
                    metadata={"probe": "true"},
                )
                return batch
            
            batch = exponential_backoff(_submit_probe)

            # Quick-poll for confirmation
            for _ in range(30):  # up to 5 minutes
                time.sleep(10)
                b = openai.batches.retrieve(batch.id)
                if b.status == "failed":
                    # Check if it's a token limit error
                    errors = getattr(b, "errors", None)
                    error_data = getattr(errors, "data", []) if errors else []
                    is_token_limit = any("token_limit" in (getattr(e, "code", "") or "")
                                         for e in error_data)
                    if is_token_limit:
                        log.warning(f"[Probe] Ghost tokens still blocking {model}. "
                                    f"Waiting {delay}s before next attempt...")
                        break
                    else:
                        # Failed for another reason — log and treat as blocked
                        error_msgs = [getattr(e, "message", str(e)) for e in error_data]
                        log.warning(f"[Probe] Batch failed: {error_msgs}. "
                                    f"Waiting {delay}s...")
                        break
                elif b.status in ("in_progress", "finalizing", "completed"):
                    log.info(f"[Probe] Capacity confirmed! Batch reached {b.status}.")
                    # Cancel the probe — we don't need its results
                    if b.status not in ("completed", "failed", "cancelled", "expired"):
                        try:
                            openai.batches.cancel(batch.id)
                        except Exception:
                            pass
                    # Clean up probe file
                    try:
                        os.remove(probe_path)
                    except OSError:
                        pass
                    return True
            else:
                # Still validating after 5 min — treat as tentatively OK
                log.info(f"[Probe] Batch still validating after 5 min. Assuming capacity is available.")
                try:
                    openai.batches.cancel(batch.id)
                except Exception:
                    pass
                try:
                    os.remove(probe_path)
                except OSError:
                    pass
                return True

        except RateLimitExceeded as e:
            log.warning(f"[Probe] Rate limited: {e}. Waiting {delay}s...")
        except Exception as e:
            log.warning(f"[Probe] Error: {e}. Waiting {delay}s...")

        time.sleep(delay)
        delay = min(delay * 2, 1800)  # cap at 30 min

    log.error(f"[Probe] Enqueued capacity for {model} still blocked after {max_wait}s. Giving up.")
    try:
        os.remove(probe_path)
    except OSError:
        pass
    return False


def poll_batch_status(batch_id: str, timeout_seconds: int = 10800) -> dict:
    """Polls batch status every 60s. Cancels if no progress in `timeout_seconds`. Waits for confirmed cancellation."""
    previous_completed = 0
    last_progress_time = time.time()

    while True:
        batch = openai.batches.retrieve(batch_id)
        status = batch.status
        request_counts = batch.request_counts
        completed = getattr(request_counts, "completed", 0)
        total = getattr(request_counts, "total", 0)

        log.info(f"Batch {batch_id} status: {status} — {completed}/{total} completed")

        if status in {"completed", "failed", "expired"}:
            return {"status": status, "batch": batch}

        # Reset timeout if there's progress
        if completed > previous_completed:
            last_progress_time = time.time()
            previous_completed = completed

        # Cancel if no progress for too long
        if time.time() - last_progress_time > timeout_seconds:
            log.warning(f"No progress in last {timeout_seconds // 60} mins. Cancelling batch {batch_id}...")
            try:
                openai.batches.cancel(batch_id)
            except Exception as e:
                log.error(f"Error cancelling batch {batch_id}: {e}")

            # Wait for cancellation confirmation
            while True:
                batch = openai.batches.retrieve(batch_id)
                log.info(f"Waiting for cancellation... Current status: {batch.status}")
                if batch.status in {"cancelled", "failed", "expired"}:
                    return {"status": "cancelled", "batch": batch}
                time.sleep(60)

        time.sleep(60)


def download_batch_results(batch_id: str, save_path: str):
    """Downloads and stores the results of a completed batch job."""
    def _download():
        batch = openai.batches.retrieve(batch_id)
        if batch.status != "completed":
            raise RuntimeError(f"Batch {batch_id} not completed.")

        output_file_id = batch.output_file_id  # ← updated attribute
        if not output_file_id:
            raise RuntimeError(f"No output file found for batch {batch_id}.")

        output_file = openai.files.retrieve(output_file_id)
        response = openai.files.content(output_file_id)

        with open(save_path, "wb") as f:
            f.write(response.read())

        log.info(f"Saved results to {save_path}")
    
    return exponential_backoff(_download)


def download_batch_results_if_available(batch_id: str, save_path: str) -> bool:
    """Downloads results from a batch if output_file_id exists, regardless of batch status.

    This handles expired batches that may have partial results which
    download_batch_results() cannot retrieve (it requires status == 'completed').
    """
    def _download():
        batch = openai.batches.retrieve(batch_id)
        output_file_id = batch.output_file_id

        if not output_file_id:
            log.warning(f"No output file available for batch {batch_id} (status: {batch.status})")
            return False

        response = openai.files.content(output_file_id)
        with open(save_path, "wb") as f:
            f.write(response.read())

        log.info(f"Saved available results from batch {batch_id} (status: {batch.status}) to {save_path}")
        return True
    
    try:
        return exponential_backoff(_download)
    except Exception as e:
        log.warning(f"Failed to download batch results: {e}")
        return False


def get_processed_custom_ids(result_path: str) -> set:
    """Reads a results JSONL file and returns the set of custom_id values that were processed."""
    processed = set()
    with open(result_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                result = json.loads(line)
                custom_id = result.get("custom_id")
                if custom_id:
                    processed.add(custom_id)
            except (json.JSONDecodeError, KeyError):
                continue
    return processed


def add_estimated_batch_cost(requests: list[dict], model: str):
    """Estimate and record the cost of the batch job using accurate pricing.
    
    Applies 50% discount for Batch API usage.
    """
    # Per 1M token pricing in USD
    pricing = {
        "gpt-4.1": {"input": 0.0020, "output": 0.0080},
        "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    }

    discount_factor = 0.5  # 50% discount for batch jobs

    # Default fallback pricing
    model_pricing = pricing.get(model, {"input": 0.0010, "output": 0.0010})

    input_tokens = sum(req.get("meta", {}).get("estimated_tokens", 300) for req in requests)
    output_tokens = len(requests) * 300  # Assumes ~300 output tokens per completion

    input_cost = (input_tokens / 200_000) * model_pricing["input"] * discount_factor
    output_cost = (output_tokens / 500_000) * model_pricing["output"] * discount_factor
    estimated_cost = input_cost + output_cost

    log.info(f"Estimated cost for batch (input + output): ${estimated_cost:.4f}")
    add_cost(estimated_cost)
