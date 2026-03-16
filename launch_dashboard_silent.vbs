Set WshShell = CreateObject("WScript.Shell")
scriptPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\\launch_dashboard.bat"
WshShell.Run Chr(34) & scriptPath & Chr(34), 0, False
