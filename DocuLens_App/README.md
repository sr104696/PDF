# DocuLens OCR Application

A modern, local PDF OCR (Optical Character Recognition) application built with FastAPI and a beautiful vanilla JavaScript frontend. Upload PDF files and get searchable, OCR-processed PDFs back in seconds.

![DocuLens](https://img.shields.io/badge/version-2.0.0-blue)
![Python](https://img.shields.io/badge/python-3.8+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## Features

✨ **Drag & Drop Interface** - Beautiful modern UI with drag-and-drop file upload  
⚡ **Real-time Progress** - Live progress updates via Server-Sent Events (SSE)  
🔍 **High-Quality OCR** - Uses Tesseract OCR engine with 300 DPI rendering  
📄 **PDF Preservation** - Maintains original layout with invisible text layer  
🛡️ **File Validation** - Automatic file type and size validation  
🧹 **Auto Cleanup** - Memory management with automatic job cleanup  
📊 **Health Monitoring** - Built-in health check endpoint  
🖥️ **Desktop App Mode** - Opens in Chrome app mode for native-like experience  

## Prerequisites

### Required Software

1. **Python 3.8 or higher**
   - Download from [python.org](https://www.python.org/downloads/)

2. **Tesseract OCR Engine**
   - **Windows**: [Download installer](https://github.com/UB-Mannheim/tesseract/wiki)
     - Recommended: Install to `C:\Program Files\Tesseract-OCR\`
   - **macOS**: `brew install tesseract`
   - **Linux**: `sudo apt-get install tesseract-ocr` (Ubuntu/Debian) or `sudo dnf install tesseract` (Fedora)

3. **Google Chrome** (for desktop app mode)

## Installation

### 1. Navigate to the App Folder

```bash
cd DocuLens_App
```

### 2. Create Virtual Environment (Recommended)

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment (Optional)

Create a `.env` file to customize settings:

```
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
MAX_FILE_SIZE_MB=50
JOB_TIMEOUT_MINUTES=30
PORT=8000
HOST=127.0.0.1
```

## Usage

### Quick Start (Linux/Ubuntu)

1. Make the launcher executable (if not already):
   ```bash
   chmod +x start_doculens.sh
   ```

2. Run the launcher:
   ```bash
   ./start_doculens.sh
   ```

3. App opens in Chrome app mode automatically!

4. Drag & drop a PDF, wait for processing, and it auto-downloads to your Downloads folder.

### Creating Desktop Shortcut (Linux)

Create a `.desktop` file at `~/.local/share/applications/doculens.desktop`:

```ini
[Desktop Entry]
Name=DocuLens OCR
Comment=PDF OCR Application
Exec=/workspace/DocuLens_App/start_doculens.sh
Icon=application-pdf
Terminal=false
Type=Application
Categories=Utility;
```

Then it will appear in your application menu!

### Quick Start (Windows)

Double-click `start_doculens.vbs` to automatically:
1. Clear port 8000 if occupied
2. Start the OCR server
3. Open Chrome in app mode (creates a desktop-app-like window)

### Manual Start

```bash
# Start the server
python ocr_server.py

# Or using uvicorn directly
uvicorn ocr_server:app --host 127.0.0.1 --port 8000 --reload
```

Then open your browser to: `http://localhost:8000`

### Using the App

1. **Drag & Drop** a PDF file onto the upload zone, or click to browse
2. **Wait** while the OCR processing completes (progress shown in real-time)
3. **Download** automatically starts when processing is complete - file saves to your Downloads folder
4. **Done!** Your searchable PDF is ready

### Creating a Desktop Shortcut (Windows)

To create a desktop shortcut for easy access:

1. Right-click on `start_doculens.vbs`
2. Select "Send to" → "Desktop (create shortcut)"
3. Optionally rename the shortcut to "DocuLens OCR"

Or run this command in PowerShell (as Administrator):
```powershell
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$Home\Desktop\DocuLens OCR.lnk")
$Shortcut.TargetPath = "$(Get-Location)\start_doculens.vbs"
$Shortcut.WorkingDirectory = "$(Get-Location)"
$Shortcut.Save()
```

## API Documentation

### Endpoints

#### Health Check
```bash
GET /health
```
Returns server status and active job count.

#### Upload PDF
```bash
POST /api/upload
Content-Type: multipart/form-data

Parameters:
- file: PDF file to process

Response:
{
  "job_id": "uuid-string"
}
```

#### Get Progress (SSE)
```bash
GET /api/progress/{job_id}
```
Server-Sent Events stream returning real-time progress updates.

Response format:
```json
{
  "status": "processing",
  "progress": 45
}
```

Status values: `queued`, `processing`, `completed`, `error`

#### Download Result
```bash
GET /api/download/{job_id}
```
Downloads the OCR-processed PDF file to your Downloads folder.

#### Cancel Job
```bash
DELETE /api/job/{job_id}
```
Cancels a running job and cleans up resources.

### API Testing Examples

```bash
# Health check
curl http://localhost:8000/health

# Upload a PDF
curl -X POST -F "file=@document.pdf" http://localhost:8000/api/upload

# Monitor progress (in another terminal)
curl http://localhost:8000/api/progress/<job_id>

# Download result
curl -O http://localhost:8000/api/download/<job_id>
```

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TESSERACT_PATH` | Path to Tesseract executable | Auto-detected |
| `MAX_FILE_SIZE_MB` | Maximum upload file size in MB | 50 |
| `JOB_TIMEOUT_MINUTES` | Job timeout in minutes | 30 |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | INFO |
| `PORT` | Server port | 8000 |
| `HOST` | Server host | 127.0.0.1 |

### Customizing OCR Settings

Edit the `config` parameter in `ocr_server.py`:

```python
pytesseract.image_to_pdf_or_hocr(
    img, 
    extension='pdf', 
    lang='eng',  # Change language code here
    config='--psm 1 --oem 3'  # Page segmentation and OCR engine mode
)
```

Common language codes: `eng`, `fra`, `deu`, `spa`, `ita`, `por`, `rus`, `chi_sim`, `jpn`

## Troubleshooting

### Tesseract Not Found

**Error**: `TesseractNotFoundError: tesseract is not installed or it's not in your path`

**Solution**:
1. Install Tesseract OCR (see Prerequisites)
2. Set the `TESSERACT_PATH` environment variable:
   ```bash
   # Windows
   set TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
   
   # macOS/Linux
   export TESSERACT_PATH=/usr/bin/tesseract
   ```

### Port Already in Use

**Error**: `Address already in use`

**Solution**:
```bash
# Windows - Kill process on port 8000
for /f "tokens=5" %a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do taskkill /f /pid %a

# Linux/macOS
lsof -ti:8000 | xargs kill -9
```

Or change the port in `.env`:
```
PORT=8001
```

### Large Files Fail

**Error**: `File too large`

**Solution**: Increase `MAX_FILE_SIZE_MB` in `.env`:
```
MAX_FILE_SIZE_MB=100
```

### Poor OCR Quality

**Tips**:
- Ensure source PDF has sufficient resolution (at least 150 DPI)
- Try different `--psm` values in the OCR config
- For multi-language documents, specify all languages: `lang='eng+fra'`

### Chrome Not Opening

If Chrome doesn't open automatically:
1. Make sure Chrome is installed
2. Manually navigate to `http://localhost:8000` in your browser
3. Check that the server is running (look for console output)

## Project Structure

```
DocuLens_App/
├── ocr_server.py          # Main FastAPI backend
├── static/
│   └── index.html         # Frontend HTML/CSS/JS
├── requirements.txt       # Python dependencies
├── runner.bat             # Windows startup script
├── start_doculens.vbs     # Windows auto-launcher with Chrome app mode
├── uploads/               # Temporary upload storage (created at runtime)
└── outputs/               # OCR output files (created at runtime)
```

## Development

### Running in Development Mode

```bash
uvicorn ocr_server:app --reload --log-level debug
```

### Code Style

This project follows PEP 8 guidelines. Use `black` for formatting:

```bash
pip install black
black ocr_server.py
```

## Security Considerations

⚠️ **Important**: This application is designed for **local use only**. 

For production deployment:
- Restrict CORS origins in `ocr_server.py`
- Add authentication/authorization
- Enable HTTPS
- Implement rate limiting
- Add input sanitization
- Use environment variables for secrets
- Regular security updates

## Performance Tips

- **DPI Setting**: Lower DPI (e.g., 200) for faster processing, higher (300+) for better accuracy
- **Batch Processing**: Process multiple small files instead of one large file
- **Memory**: Close other applications when processing large PDFs
- **CPU**: OCR is CPU-intensive; performance scales with cores

## Changelog

### Version 2.0.0 (Unified)
- ✅ Fixed SSE string formatting bug
- ✅ Added job cleanup mechanism
- ✅ Implemented proper error handling
- ✅ Added file validation and size limits
- ✅ Made Tesseract path configurable
- ✅ Added health check endpoint
- ✅ Added job timeout protection
- ✅ Improved logging
- ✅ Cross-platform support
- ✅ Enhanced error display in UI
- ✅ Unified codebase from multiple versions

### Version 1.0.0 (Original)
- Initial release

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

MIT License - See LICENSE file for details

## Support

For issues and questions:
- Check the troubleshooting section above
- Review the server logs for error messages
- Ensure Tesseract OCR is properly installed

## Acknowledgments

- [FastAPI](https://fastapi.tiangolo.com/) - Modern web framework
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) - OCR engine
- [PyMuPDF](https://pymupdf.readthedocs.io/) - PDF processing
- [pytesseract](https://github.com/madmaze/pytesseract) - Python wrapper

---

Made with ❤️ for document digitization
