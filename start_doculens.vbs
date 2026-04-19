Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")

WshShell.CurrentDirectory = strPath
' Launch backend silently via runner file that manages port clearing
WshShell.Run "cmd.exe /c runner.bat", 0, False

' Give it 2 seconds to start uvicorn
WScript.Sleep 2000

' Launch Chrome in app mode
WshShell.Run "chrome.exe --app=http://localhost:8000", 1, False
