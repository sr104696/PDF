' DocuLens OCR App Launcher
' This script starts the OCR server and opens Chrome in app mode

Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")

WshShell.CurrentDirectory = strPath

' Launch backend silently via runner file that manages port clearing
WshShell.Run "cmd.exe /c runner.bat", 0, False

' Give it 2 seconds to start uvicorn
WScript.Sleep 2000

' Launch Chrome in app mode - this creates a desktop-app-like experience
WshShell.Run "chrome.exe --app=http://localhost:8000", 1, False
