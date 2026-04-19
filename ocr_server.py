import os
import uuid
import asyncio
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import fitz  # PyMuPDF
from PIL import Image
import pytesseract

# Configure Tesseract path for Windows
default_tess = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(default_tess):
    pytesseract.pytesseract.tesseract_cmd = default_tess

app = FastAPI(title="Local OCR App")

# Allow CORS if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
OUTPUT_DIR = os.path.join(os.getcwd(), "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

jobs = {}

def run_pytesseract(input_path: str, output_path: str, job_id: str):
    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["progress"] = 15
        
        doc = fitz.open(input_path)
        ocr_doc = fitz.open()  # new PDF
        total_pages = len(doc)
        
        if total_pages == 0:
            raise Exception("PDF has no pages.")
            
        for i, page in enumerate(doc, start=1):
            # Update progress based on pages handled
            current_progress = 15 + int((i - 1) / total_pages * 80)
            jobs[job_id]["progress"] = current_progress
            
            # render page to high-res image
            pix = page.get_pixmap(dpi=300, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # OCR to PDF (keeps image + adds invisible text layer)
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                img, extension='pdf', lang='eng', config='--psm 1 --oem 3'
            )
            
            # insert OCR'd page
            img_pdf = fitz.open("pdf", pdf_bytes)
            ocr_doc.insert_pdf(img_pdf)
        
        # Finalize
        jobs[job_id]["progress"] = 95
        ocr_doc.set_metadata(doc.metadata)
        ocr_doc.save(str(output_path), garbage=4, deflate=True)
        ocr_doc.close()
        doc.close()
        
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/api/upload")
async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    filename = file.filename if file.filename else "document.pdf"
    file_prefix, ext = os.path.splitext(filename)
    
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
    output_path = os.path.join(OUTPUT_DIR, f"{file_prefix}_ocr{ext}")
    
    # Save the uploaded file
    with open(input_path, "wb") as f:
        f.write(await file.read())
        
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "input_path": input_path,
        "output_path": output_path,
        "filename": f"{file_prefix}_ocr{ext}"
    }
    
    background_tasks.add_task(run_pytesseract, input_path, output_path, job_id)
    
    return {"job_id": job_id}

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    async def event_generator():
        while True:
            if job_id not in jobs:
                yield {"data": '{"status": "not_found"}'}
                break
                
            job = jobs[job_id]
            data = f'{{"status": "{job["status"]}", "progress": {job["progress"]}}}'
            yield {"data": data}
            
            if job["status"] in ["completed", "error"]:
                break
                
            await asyncio.sleep(0.5)
            
    return EventSourceResponse(event_generator())

@app.get("/api/download/{job_id}")
async def download_pdf(job_id: str):
    if job_id not in jobs:
        return {"error": "Job not found"}
        
    job = jobs[job_id]
    if job["status"] == "completed" and os.path.exists(job["output_path"]):
        return FileResponse(
            path=job["output_path"], 
            filename=job["filename"], 
            media_type="application/pdf"
        )
    return {"error": "File not ready or failed"}

# Mount static files at root
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
