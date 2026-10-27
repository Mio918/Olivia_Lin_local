' ============================================================
'  BSide local mock server - SILENT LAUNCHER
'  Double-click to start the server with NO console window.
'
'  - log goes to server.log (open it if something breaks)
'  - if the server is already up, it just tells you and exits
'  - silent mode for scripts: cscript //nologo START_SILENT.vbs /q
' ============================================================
Option Explicit

Dim gQuiet, i
gQuiet = False
For i = 0 To WScript.Arguments.Count - 1
    If LCase(WScript.Arguments(i)) = "/q" Or LCase(WScript.Arguments(i)) = "-q" Then gQuiet = True
Next

Dim fso, sh, scriptDir, logPath, py, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
logPath = scriptDir & "\server.log"

If Not fso.FileExists(scriptDir & "\server.py") Then
    Say "server.py not found next to this script:" & vbCrLf & scriptDir
    WScript.Quit 1
End If

If IsUp() Then
    Say "Already running on http://127.0.0.1:8787 - nothing to do."
    WScript.Quit 0
End If

py = FindPython()
If py = "" Then
    Say "Python 3.12 not found. Install it or edit PY_CANDIDATES in this script."
    WScript.Quit 1
End If

' Window style 0 = hidden; stdout/stderr appended to server.log.
cmd = "cmd /c cd /d """ & scriptDir & """ && set PYTHONUTF8=1&& set PYTHONIOENCODING=utf-8&& """ & py & """ server.py --port 8787 1>>""" & logPath & """ 2>&1"
sh.Run cmd, 0, False

WScript.Sleep 1800

If IsUp() Then
    Say "Server started (background, no window)." & vbCrLf & vbCrLf & _
        "http://127.0.0.1:8787" & vbCrLf & _
        "Log: " & logPath & vbCrLf & vbCrLf & _
        "Stop it with STOP_SILENT.vbs"
    WScript.Quit 0
Else
    Say "Started, but /health is not answering yet." & vbCrLf & _
        "Check server.log for the reason (port taken? python error?)." & vbCrLf & vbCrLf & logPath
    WScript.Quit 2
End If


' ---------------- helpers ----------------

Sub Say(msg)
    If Not gQuiet Then MsgBox msg, 64, "BSide mock server"
End Sub

Function IsUp()
    Dim x
    IsUp = False
    On Error Resume Next
    Set x = CreateObject("MSXML2.XMLHTTP")
    x.Open "GET", "http://127.0.0.1:8787/health", False
    x.Send
    If Err.Number = 0 Then
        If x.Status = 200 Then IsUp = True
    End If
    Err.Clear
    On Error GoTo 0
End Function

Function FindPython()
    Dim candidates, c
    candidates = Array( _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\python.exe", _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\python.exe", _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\python.exe", _
        "C:\Python312\python.exe", _
        "C:\Python311\python.exe" _
    )
    FindPython = ""
    For Each c In candidates
        If fso.FileExists(c) Then
            FindPython = c
            Exit Function
        End If
    Next
End Function
