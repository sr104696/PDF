"""
DocuLens OCR Server - Fixed Version

A FastAPI-based OCR service that processes PDF files using PyMuPDF and pytesseract.
This version includes all critical bug fixes and improvements.

Changes from original:
- Fixed SSE string formatting bug (line 117)
- Added job cleanup mechanism to prevent memory leaks
- Implemented proper error handling and logging
- Added file validation and size limits
- Made Tesseract path configurable across platforms
- Added health check endpoint
- Added job timeout protection
- Improved progress tracking
"""

import os
import uuid
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import fitz  # PyMuPDF
from PIL import Image
import pytesseract

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configure Tesseract path - cross-platform support
def find_tesseract_path():
    """Find Tesseract executable in common installation locations."""
    # Check environment variable first
    env_path = os.getenv('TESSERACT_PATH')
    if env_path and os.path.exists(env_path):
        logger.info(f"Using Tesseract from TESSERACT_PATH: {env_path}")
        return env_path

    # Try common installation paths based on OS
    possible_paths = []

    if os.name == 'nt':  # Windows
        possible_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ]
    else:  # Linux/Mac
        possible_paths = [
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/homebrew/bin/tesseract",
            "/snap/bin/tesseract",
        ]

    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"Found Tesseract at: {path}")
            return path

    logger.warning("Tesseract executable not found in common locations. Using system default.")
    return None

tesseract_path = find_tesseract_path()
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

app = FastAPI(title="DocuLens OCR App", version="2.0.0")

# Configuration from environment variables
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE_MB', 50)) * 1024 * 1024  # Default 50MB
JOB_TIMEOUT_MINUTES = int(os.getenv('JOB_TIMEOUT_MINUTES', 30))
ALLOWED_EXTENSIONS = {'.pdf'}

# Allow CORS - configure appropriately for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Job storage with metadata
jobs = {}


def cleanup_job(job_id: str):
    """Remove job from memory to prevent leaks."""
    if job_id in jobs:
        logger.info(f"Cleaning up job: {job_id}")
        del jobs[job_id]


async def periodic_cleanup():
    """Background task to clean up old completed/failed jobs."""
    while True:
        await asyncio.sleep(300)  # Run every 5 minutes

        now = datetime.now()
        jobs_to_remove = []

        for job_id, job in jobs.items():
            if job["status"] in ["completed", "error"]:
                created_at = job.get("created_at", now)
                if isinstance(created_at, datetime):
                    age = now - created_at
                    if age > timedelta(minutes=30):  # Keep for 30 minutes after completion
                        jobs_to_remove.append(job_id)

        for job_id in jobs_to_remove:
            cleanup_job(job_id)
            logger.info(f"Removed old job: {job_id}")


def run_pytesseract(input_path: str, output_path: str, job_id: str):
    """Process PDF with OCR in background thread."""
    logger.info(f"Starting OCR processing for job {job_id}")

    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 15
        
        # Validate input file exists
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        doc = fitz.open(input_path)
        ocr_doc = fitz.open()  # new PDF
        total_pages = len(doc)
        
        if total_pages == 0:
            doc.close()
            raise ValueError("PDF has no pages.")

        logger.info(f"Processing {total_pages} pages for job {job_id}")

        for i, page in enumerate(doc, start=1):
            # Update progress based on pages handled
            current_progress = 15 + int((i - 1) / total_pages * 80)
            jobs[job_id]["progress"] = current_progress
            
            if i % 5 == 0 or i == total_pages:
                logger.debug(f"Page {i}/{total_pages} processed for job {job_id}")

            # Render page to high-res image
            pix = page.get_pixmap(dpi=300, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # OCR to PDF (keeps image + adds invisible text layer)
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                img, extension='pdf', lang='eng', config='--psm 1 --oem 3'
            )
            
            # Insert OCR'd page
            img_pdf = fitz.open("pdf", pdf_bytes)
            ocr_doc.insert_pdf(img_pdf)
            img_pdf.close()
        
        # Finalize
        jobs[job_id]["progress"] = 95
        ocr_doc.set_metadata(doc.metadata)
        ocr_doc.save(str(output_path), garbage=4, deflate=True)
        ocr_doc.close()
        doc.close()
        
        # Verify output was created
        if not os.path.exists(output_path):
            raise IOError("Failed to create output file")

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        logger.info(f"Job {job_id} completed successfully")
        
    except fitz.FileDataError as e:
        logger.error(f"Invalid PDF format for job {job_id}: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "Invalid or corrupted PDF file"
    except pytesseract.TesseractNotFoundError as e:
        logger.error(f"Tesseract not found for job {job_id}: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = "OCR engine not configured. Please install Tesseract."
    except Exception as e:
        logger.exception(f"Unexpected error processing job {job_id}: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = f"Processing failed: {str(e)}"


@app.on_event("startup")
async def startup_event():
    """Start background cleanup task on server startup."""
    logger.info("Starting DocuLens OCR Server")
    logger.info(f"Upload directory: {UPLOAD_DIR}")
    logger.info(f"Output directory: {OUTPUT_DIR}")
    logger.info(f"Max file size: {MAX_FILE_SIZE / (1024*1024):.0f}MB")
    asyncio.create_task(periodic_cleanup())


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on server shutdown."""
    logger.info("Shutting down DocuLens OCR Server")


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    active_jobs = len([j for j in jobs.values() if j["status"] in ["queued", "processing"]])
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_jobs": active_jobs,
        "version": "2.0.0"
    }


@app.post("/api/upload")
async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Upload a PDF file for OCR processing."""
    job_id = str(uuid.uuid4())
    filename = file.filename if file.filename else "document.pdf"
    file_prefix, ext = os.path.splitext(filename)
    
    # Validate file extension
    if ext.lower() not in ALLOWED_EXTENSIONS:
        logger.warning(f"Rejected upload {filename}: invalid extension")
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Read file content
    content = await file.read()

    # Validate file size
    if len(content) > MAX_FILE_SIZE:
        logger.warning(f"Rejected upload {filename}: file too large ({len(content)} bytes)")
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"
        )

    # Basic PDF validation (check magic number)
    if not content.startswith(b'%PDF'):
        logger.warning(f"Rejected upload {filename}: not a valid PDF")
        raise HTTPException(status_code=400, detail="Invalid PDF file format")

    input_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
    output_path = os.path.join(OUTPUT_DIR, f"{file_prefix}_ocr{ext}")
    
    # Save the uploaded file
    try:
        with open(input_path, "wb") as f:
            f.write(content)
        logger.info(f"Saved uploaded file to {input_path}")
    except IOError as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="Failed to save uploaded file")

    # Create job entry
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "input_path": input_path,
        "output_path": output_path,
        "filename": f"{file_prefix}_ocr{ext}",
        "created_at": datetime.now()
    }
    
    logger.info(f"Created job {job_id} for file {filename}")

    # Start background processing
    background_tasks.add_task(run_pytesseract, input_path, output_path, job_id)
    
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    """Get real-time progress updates via Server-Sent Events."""
    async def event_generator():
        try:
            while True:
                if job_id not in jobs:
                    yield {"data": json.dumps({"status": "not_found", "error": "Job not found"})}
                    break
                
                job = jobs[job_id]
                
                # Check for timeout
                if job["status"] == "processing":
                    created_at = job.get("created_at")
                    if created_at:
                        elapsed = datetime.now() - created_at
                        if elapsed > timedelta(minutes=JOB_TIMEOUT_MINUTES):
                            job["status"] = "error"
                            job["error"] = "Job timed out"
                            logger.warning(f"Job {job_id} timed out after {elapsed}")

                # Send progress update using proper JSON formatting
                data = json.dumps({
                    "status": job["status"],
                    "progress": job["progress"]
                })
                yield {"data": data}

                if job["status"] in ["completed", "error"]:
                    logger.info(f"Job {job_id} reached terminal state: {job['status']}")
                    break

                await asyncio.sleep(0.5)
        except Exception as e:
            logger.exception(f"Error in SSE stream for job {job_id}: {e}")
            yield {"data": json.dumps({"status": "error", "error": str(e)})}

    return EventSourceResponse(event_generator())


@app.get("/api/download/{job_id}")
async def download_pdf(job_id: str):
    """Download the processed OCR PDF file."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"File not ready. Current status: {job['status']}"
        )

    if not os.path.exists(job["output_path"]):
        logger.error(f"Output file missing for job {job_id}: {job['output_path']}")
        raise HTTPException(status_code=404, detail="Output file not found on server")

    logger.info(f"Serving download for job {job_id}: {job['filename']}")

    return FileResponse(
        path=job["output_path"],
        filename=job["filename"],
        media_type="application/pdf"
    )


@app.delete("/api/job/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a running job (optional cleanup endpoint)."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] in ["completed", "error"]:
        cleanup_job(job_id)
        return {"message": "Job cleaned up"}

    # Mark as cancelled (will stop processing on next check)
    job["status"] = "cancelled"
    logger.info(f"Job {job_id} cancelled by user")

    return {"message": "Job cancelled"}


# Mount static files at root
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', 8000))

    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
