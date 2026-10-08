' run_hidden.vbs -- start a command with an invisible (hidden) console window.
' Used by the Windows scheduled tasks so no black terminal window flashes.
'
' Usage:
'   wscript.exe //nologo run_hidden.vbs "C:\Python314\python.exe" "D:\path\script.py" --flag
'
' The console still exists, it is only never shown, so child processes inherit
' the hidden console and do not pop up windows of their own.
Option Explicit

Dim shell, cmd, i

If WScript.Arguments.Count = 0 Then
    WScript.Quit 2
End If

cmd = Quote(WScript.Arguments(0))
For i = 1 To WScript.Arguments.Count - 1
    cmd = cmd & " " & Quote(WScript.Arguments(i))
Next

Set shell = CreateObject("WScript.Shell")
' 0 = hidden window, True = wait, so the scheduled task keeps tracking the real
' process and "ignore new instance" still works.
shell.Run cmd, 0, True

Function Quote(value)
    Quote = """" & Replace(value, """", """""") & """"
End Function
