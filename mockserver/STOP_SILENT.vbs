' ============================================================
'  BSide local mock server - STOPPER
'  Double-click to stop the background server.
'  Silent mode: cscript //nologo STOP_SILENT.vbs /q
' ============================================================
Option Explicit

Dim gQuiet, i
gQuiet = False
For i = 0 To WScript.Arguments.Count - 1
    If LCase(WScript.Arguments(i)) = "/q" Or LCase(WScript.Arguments(i)) = "-q" Then gQuiet = True
Next

Dim wmi, procs, p, killed
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId, Name, CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe' OR Name='py.exe'")

killed = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        ' only touch our own server, never other python jobs
        If InStr(p.CommandLine, "server.py") > 0 And InStr(p.CommandLine, "8787") > 0 Then
            On Error Resume Next
            p.Terminate()
            If Err.Number = 0 Then killed = killed + 1
            Err.Clear
            On Error GoTo 0
        End If
    End If
Next

If killed = 0 Then
    Say "Nothing to stop (server is not running)."
Else
    Say "Stopped " & killed & " process(es). Port 8787 released."
End If


Sub Say(msg)
    If Not gQuiet Then MsgBox msg, 64, "BSide mock server"
End Sub
