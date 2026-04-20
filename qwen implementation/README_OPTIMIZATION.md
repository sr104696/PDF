# AI & Search Engineering Optimization Report

## Overview
This document summarizes the optimizations made to the Reddit Automation system's intelligence layers (Stages 04 and 05) for unified data source integration, cost savings, and improved rate limiting.

---

## 1. AI Batch Optimization (Stage 04)

### File: `batch_api.py`
**Location:** `qwen implementation/batch_api.py`

#### Key Improvements:
- **Strict Batch API Usage**: All LLM calls now exclusively use OpenAI Batch API - no synchronous calls allowed
- **50% Cost Savings**: Applied `discount_factor = 0.5` in cost estimation for batch jobs
- **Exponential Backoff**: Implemented `exponential_backoff()` function for all API calls with configurable:
  - Initial delay (default: 1s)
  - Max delay (default: 300s)
  - Multiplier (default: 2x)
  - Max retries (default: 5)
- **RateLimitExceeded Exception**: Custom exception class for better error handling
- **Protected API Calls**: All OpenAI operations wrapped with retry logic

---

## 2. Configuration Updates

### File: `config.yaml`
**Location:** `qwen implementation/config.yaml`

#### New Scoring Weights:
```yaml
scoring:
  relevance_weight: 0.20
  emotion_weight: 0.10
  pain_point_weight: 0.18
  implementability_weight: 0.12
  technical_depth_weight: 0.15
  technical_moat_weight: 0.15       # NEW METRIC
  recent_activity_weight: 0.10
  min_technical_depth: 4
  min_technical_moat: 3             # NEW HARD FILTER
  output_top_n: 10
```

#### Technical Moat Metric:
- Measures defensibility/competitive advantage of product ideas
- Hard filter rejects posts below score of 3
- Weighted at 15% in final scoring

#### Rate Limiting Configuration:
```yaml
rate_limiting:
  praw:
    bucket_capacity: 60
    refill_rate: 1.0
  llm:
    initial_delay: 1.0
    max_delay: 300.0
    multiplier: 2.0
    max_retries: 5
```

---

## 3. RAG Integration (Stage 05)

### File: `rag_manager.py`
**Location:** `qwen implementation/rag_manager.py`

#### Key Features:
- **Unified SQLite Database Integration**: Connects directly to `reddit_leads.db`
- **Incremental Updates**: Only processes rows that haven't been indexed
- **Database Methods**:
  - `_get_indexed_ids_from_db()`: Queries database for already-indexed IDs
  - `_mark_as_indexed()`: Updates database when new embeddings are created
  - `fetch_unindexed_data()`: Efficiently queries only unindexed posts
- **Idempotent Operations**: Safe to run multiple times without duplication
- **Migration Support**: Handles both new `embedding_indexed` column and legacy NULL-check approach

#### Incremental Update Flow:
1. Query database for posts where `embedding_indexed = 0` OR `embedding IS NULL`
2. Generate embeddings only for new posts
3. Append to existing embeddings array
4. Mark processed posts as indexed in database
5. Save metadata for persistence

---

## 4. Feature Implementation: URL Logging

### File: `main.py`
**Location:** `qwen implementation/main.py`

#### Post History Logging:
- **Log File**: `qwen implementation/post_history.txt`
- **Format**: `{ISO_TIMESTAMP} | {REDDIT_URL}`
- **Trigger**: Every successful post save operation
- **Function**: `log_post_url(url)`

#### Example Log Entry:
```
2025-01-15T10:30:45.123456 | https://reddit.com/r/devops/comments/abc123/my_post_title
```

---

## 5. Rate Limit Efficiency

### Token Bucket Implementation
**File:** `rate_limiter.py`

#### For PRAW/Reddit API (`02_Heavy_Extractor`):
- **TokenBucket Class**: 
  - Capacity: 60 tokens (requests per minute)
  - Refill rate: 1 token/second
  - Blocking acquisition with timeout support
- **Integration**: `praw_limiter.wait_and_acquire()` called before each API request

#### For LLM API Calls (`04_AI_Scoring_Layer`):
- **ExponentialBackoff Class**:
  - Initial delay: 1 second
  - Max delay: 300 seconds (5 minutes)
  - Multiplier: 2x
  - Jitter: ±25% randomization to prevent thundering herd
- **Integration**: All OpenAI API calls wrapped in `exponential_backoff()`

#### Combined Approach:
```python
class RateLimitedPRAW:
    """Combines token bucket + exponential backoff"""
    def __init__(self, reddit_client):
        self.bucket = TokenBucket(capacity=60, refill_rate=1)
        self.backoff = ExponentialBackoff(...)
    
    def _rate_limited_call(self, func, *args, **kwargs):
        self.bucket.acquire()  # Rate limit
        return self.backoff.execute(func, *args, **kwargs)  # Retry on error
```

---

## 6. Airflow Idempotency Standards

All optimized tasks follow these idempotency principles:

1. **Duplicate Detection**: Check `SEEN_URLS` set before processing
2. **Database Upserts**: Use `INSERT OR REPLACE` / `ON CONFLICT DO UPDATE`
3. **Indexed Tracking**: Mark processed items in database to prevent re-processing
4. **Safe Retries**: All operations can be safely re-run without side effects
5. **Stateless Design**: No reliance on in-memory state between runs

---

## Files Created in `qwen implementation/` Folder

| File | Purpose |
|------|---------|
| `config.yaml` | Updated configuration with Technical Moat metric |
| `batch_api.py` | Optimized OpenAI Batch API wrapper with 50% cost savings |
| `rag_manager.py` | RAG manager with unified SQLite integration |
| `main.py` | Scraper with URL logging and rate limiting |
| `rate_limiter.py` | Token bucket + exponential backoff utilities |
| `post_history.txt` | URL log file for successful posts |

---

## Migration Notes

### Database Schema Update (for RAG incremental updates):
```sql
ALTER TABLE posts ADD COLUMN embedding_indexed INTEGER DEFAULT 0;
ALTER TABLE posts ADD COLUMN embedding_indexed_at TIMESTAMP;
CREATE INDEX idx_posts_embedding_indexed ON posts(embedding_indexed);
```

### Config Migration:
Copy `qwen implementation/config.yaml` to `04_AI_Scoring_Layer/config/config.yaml`

### Code Integration:
1. Replace `04_AI_Scoring_Layer/gpt/batch_api.py` with optimized version
2. Replace `05_Conversational_RAG_Interface/backend/rag_manager.py` with unified version
3. Add `rate_limiter.py` utilities to shared utils folder
4. Update `02_Heavy_Extractor/main.py` with URL logging feature

---

## Performance Expectations

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| LLM API Cost | 100% | 50% | 50% savings |
| Embedding Processing | Full rebuild | Incremental | ~90% faster |
| API Rate Limit Errors | Frequent | Rare | Exponential backoff |
| Duplicate Processing | Possible | Prevented | Idempotent design |

---

## Testing Checklist

- [ ] Verify Batch API submissions complete successfully
- [ ] Confirm 50% cost discount applied in billing
- [ ] Test incremental RAG updates with new posts
- [ ] Validate `post_history.txt` logging works correctly
- [ ] Stress test rate limiters under high load
- [ ] Run Airflow DAGs multiple times to verify idempotency
