# Debug Plan for DocuLens OCR Application

## Phase 1: Immediate Critical Bug Fixes (Priority: URGENT)

### Task 1.1: Fix SSE String Formatting Bug
**File**: `ocr_server.py`, Line 117  
**Current Code**:
```python
data = f'{{"status": "{job["status"]}", "progress": {job["progress"]}}}'
```

**Problem**: Nested quotes in f-string cause syntax errors or malformed JSON.

**Fix**:
```python
import json
# ...
data = json.dumps({
    "status": job["status"],
    "progress": job["progress"]
})
```

**Verification Steps**:
1. Start server
2. Upload a PDF
3. Check browser console for SSE events
4. Verify JSON parses correctly
5. Confirm progress bar updates in real-time

**Estimated Time**: 15 minutes

---

### Task 1.2: Add Job Cleanup Mechanism
**File**: `ocr_server.py`  
**Problem**: Memory leak from accumulated job states.

**Implementation Plan**:
1. Add cleanup function to remove old jobs
2. Call cleanup on job completion/error
3. Add periodic cleanup for abandoned jobs

**Code Changes**:
```python
import time
from datetime import datetime, timedelta

# Add timestamp to jobs
jobs[job_id] = {
    "status": "queued",
    "progress": 0,
    "created_at": datetime.now(),
    # ... other fields
}

# Add cleanup function
def cleanup_job(job_id):
    if job_id in jobs:
        del jobs[job_id]

# Call in download endpoint after successful download
# Add background task for periodic cleanup
```

**Verification Steps**:
1. Process 10+ files
2. Check memory usage stays stable
3. Verify jobs dict size doesn't grow indefinitely

**Estimated Time**: 30 minutes

---

### Task 1.3: Implement Proper Error Handling
**File**: `ocr_server.py`, `run_pytesseract` function

**Changes Required**:
1. Add logging configuration
2. Catch specific exceptions
3. Clean up resources on error
4. Return meaningful error messages

**Code Structure**:
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_pytesseract(input_path: str, output_path: str, job_id: str):
    try:
        # ... existing code
    except fitz.FileDataError as e:
        logger.error(f"Invalid PDF format: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Invalid PDF file"
        cleanup_job(job_id)
    except pytesseract.TesseractNotFoundError as e:
        logger.error(f"Tesseract not found: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "OCR engine not configured"
        cleanup_job(job_id)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        cleanup_job(job_id)
```

**Verification Steps**:
1. Upload corrupted PDF
2. Verify error message displayed to user
3. Check logs for detailed error
4. Confirm job cleaned up properly

**Estimated Time**: 45 minutes

---

### Task 1.4: Add File Validation and Size Limits
**File**: `ocr_server.py`, `/api/upload` endpoint

**Implementation**:
```python
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {'.pdf'}

@app.post("/api/upload")
async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    # Validate extension
    file_prefix, ext = os.path.splitext(file.filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF files allowed")
    
    # Read and check size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    
    # Validate PDF header
    if not content.startswith(b'%PDF'):
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    
    # Save file...
```

**Verification Steps**:
1. Try uploading non-PDF file → Should reject
2. Try uploading 100MB file → Should reject with 413
3. Try uploading corrupted file → Should detect and reject
4. Valid PDF → Should process normally

**Estimated Time**: 30 minutes

---

### Task 1.5: Create Requirements File
**File**: `requirements.txt` (NEW)

**Content**:
```
fastapi==0.109.0
uvicorn[standard]==0.27.0
python-multipart==0.0.6
PyMuPDF==1.23.8
Pillow==10.2.0
pytesseract==0.3.10
sse-starlette==2.0.0
```

**Verification Steps**:
1. Create fresh virtual environment
2. Run `pip install -r requirements.txt`
3. Start server
4. Verify all imports work

**Estimated Time**: 15 minutes

---

## Phase 2: Moderate Priority Improvements

### Task 2.1: Make Tesseract Path Configurable
**File**: `ocr_server.py`

**Implementation**:
```python
import os

# Check environment variable first, then default paths
tesseract_path = os.getenv('TESSERACT_PATH')
if not tesseract_path:
    # Try common installation paths
    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            tesseract_path = path
            break

if tesseract_path and os.path.exists(tesseract_path):
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
else:
    logger.warning("Tesseract executable not found. Using system default.")
```

**Verification Steps**:
1. Test on Windows with default path
2. Test with custom TESSERACT_PATH env var
3. Test on Linux/Mac (if available)

**Estimated Time**: 20 minutes

---

### Task 2.2: Add Health Check Endpoint
**File**: `ocr_server.py`

**Implementation**:
```python
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_jobs": len([j for j in jobs.values() if j["status"] in ["queued", "processing"]])
    }
```

**Verification**:
```bash
curl http://localhost:8000/health
```

**Estimated Time**: 10 minutes

---

### Task 2.3: Add Job Timeout Mechanism
**File**: `ocr_server.py`

**Implementation**:
```python
JOB_TIMEOUT_MINUTES = 30

def check_job_timeout(job_id: str):
    job = jobs.get(job_id)
    if job and job["status"] == "processing":
        elapsed = datetime.now() - job["created_at"]
        if elapsed > timedelta(minutes=JOB_TIMEOUT_MINUTES):
            job["status"] = "error"
            job["error"] = "Job timed out"
            logger.warning(f"Job {job_id} timed out")

# Call periodically or at start of processing
```

**Estimated Time**: 25 minutes

---

### Task 2.4: Remove Duplicate Files
**Action**: Delete `DocuLens_App_Copy/` folder or document its purpose

**Verification**:
1. Ensure all unique files are preserved
2. Update any references to moved files
3. Test application still works

**Estimated Time**: 10 minutes

---

### Task 2.5: Fix Frontend Issues
**File**: `static/index.html`

**Issues to Address**:
1. Add cleanup for EventSource on page unload
2. Add loading state during upload
3. Improve error messages

**Code Changes**:
```javascript
// Add beforeunload handler
window.addEventListener('beforeunload', () => {
    if (eventSource) {
        eventSource.close();
    }
});

// Add abort controller for upload cancellation
let uploadController = null;

async function uploadFile(file) {
    uploadController = new AbortController();
    // ... use signal in fetch
}
```

**Estimated Time**: 30 minutes

---

## Phase 3: Testing & Verification

### Task 3.1: Manual Testing Checklist

**Functional Tests**:
- [ ] Upload small PDF (< 1MB)
- [ ] Upload medium PDF (5-10MB)
- [ ] Upload large PDF (40-50MB)
- [ ] Upload non-PDF file (should fail)
- [ ] Upload corrupted PDF (should fail gracefully)
- [ ] Upload empty PDF (should fail gracefully)
- [ ] Multiple concurrent uploads
- [ ] Cancel upload mid-process
- [ ] Download processed file
- [ ] Verify OCR quality

**Edge Cases**:
- [ ] PDF with no text (images only)
- [ ] PDF with very small text
- [ ] PDF with multiple languages
- [ ] PDF with scanned images
- [ ] Network interruption during upload
- [ ] Server restart during processing

**Performance Tests**:
- [ ] Measure time per page
- [ ] Monitor memory usage
- [ ] Check CPU utilization
- [ ] Test with 10+ concurrent users

**Estimated Time**: 2 hours

---

### Task 3.2: Automated Testing Setup

**Create**: `tests/test_ocr_server.py`

**Test Cases**:
```python
import pytest
from fastapi.testclient import TestClient
from ocr_server import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_upload_invalid_file():
    response = client.post("/api/upload", files={"file": ("test.txt", b"not a pdf")})
    assert response.status_code == 400

def test_upload_too_large():
    # Create large file
    large_content = b"%PDF" + b"x" * (51 * 1024 * 1024)
    response = client.post("/api/upload", files={"file": ("large.pdf", large_content)})
    assert response.status_code == 413
```

**Estimated Time**: 3 hours

---

## Phase 4: Documentation & Deployment Prep

### Task 4.1: Update README
**Create**: `README.md`

**Sections**:
- Project overview
- Installation instructions
- Configuration options
- Usage guide
- API documentation
- Troubleshooting
- Contributing guidelines

**Estimated Time**: 1 hour

---

### Task 4.2: Create Environment Configuration
**Create**: `.env.example`

**Content**:
```
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
MAX_FILE_SIZE_MB=50
JOB_TIMEOUT_MINUTES=30
LOG_LEVEL=INFO
PORT=8000
HOST=127.0.0.1
```

**Estimated Time**: 15 minutes

---

### Task 4.3: Docker Support (Optional)
**Create**: `Dockerfile`

**Considerations**:
- Install Tesseract in container
- Multi-stage build for smaller image
- Volume mounts for uploads/outputs
- Health check instruction

**Estimated Time**: 2 hours

---

## Debug Tools & Commands

### Log Analysis
```bash
# Watch logs in real-time
tail -f ocr_server.log

# Search for errors
grep "ERROR" ocr_server.log

# Count job completions
grep "completed" ocr_server.log | wc -l
```

### Performance Monitoring
```bash
# Check memory usage
ps aux | grep python

# Monitor open file descriptors
lsof -p <pid> | wc -l

# Check port usage
netstat -an | grep 8000
```

### API Testing
```bash
# Health check
curl http://localhost:8000/health

# Upload file
curl -X POST -F "file=@test.pdf" http://localhost:8000/api/upload

# Check progress (in another terminal)
curl http://localhost:8000/api/progress/<job_id>
```

---

## Success Criteria

### Phase 1 Complete When:
- ✅ All critical bugs fixed
- ✅ No memory leaks detected
- ✅ Error handling works for all edge cases
- ✅ File validation prevents bad uploads
- ✅ Dependencies documented

### Phase 2 Complete When:
- ✅ Configuration is flexible
- ✅ Health monitoring available
- ✅ Timeout protection active
- ✅ Code duplication removed
- ✅ Frontend robust

### Phase 3 Complete When:
- ✅ All manual tests pass
- ✅ Automated test suite created
- ✅ Performance benchmarks met
- ✅ No critical issues in testing

### Phase 4 Complete When:
- ✅ Documentation complete
- ✅ Environment config ready
- ✅ Deployment guide written
- ✅ Team trained on fixes

---

## Risk Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Breaking changes | High | Low | Version control, backup original files |
| Tesseract compatibility | Medium | Medium | Test with target version early |
| Performance regression | Medium | Low | Benchmark before/after changes |
| New bugs introduced | Medium | Medium | Comprehensive testing, code review |

---

## Timeline Estimate

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 1 | 2-3 hours | None |
| Phase 2 | 2-3 hours | Phase 1 complete |
| Phase 3 | 3-4 hours | Phases 1-2 complete |
| Phase 4 | 2-3 hours | All phases complete |
| **Total** | **9-13 hours** | |

---

## Next Steps

1. **Immediate**: Start with Task 1.1 (SSE bug fix) - this blocks all other testing
2. **Short-term**: Complete Phase 1 within same day
3. **Medium-term**: Schedule Phase 2-3 for next development cycle
4. **Long-term**: Plan Phase 4 as part of release preparation

**Contact Points**:
- Technical questions: Review CODEBASE_REVIEW.md for context
- Bug reports: Document in issue tracker with reproduction steps
- Feature requests: Prioritize against roadmap
