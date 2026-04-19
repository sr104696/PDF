#!/bin/bash
# DocuLens Launcher for Linux

cd /workspace/DocuLens_App

# Kill any existing server on port 8000
pkill -f "python.*ocr_server.py" 2>/dev/null || true
sleep 1

# Start the server in background
python ocr_server.py > server.log 2>&1 &
SERVER_PID=$!

# Wait for server to be ready
echo "Starting DocuLens server..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "Server ready!"
        break
    fi
    sleep 1
done

# Open Chrome in app mode
google-chrome --app=http://localhost:8000 &

# Keep script running to maintain server
wait $SERVER_PID
