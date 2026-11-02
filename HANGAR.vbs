Option Explicit
Dim sh, fs, root, py, entry
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
py = fs.BuildPath(root, "runtime\pythonw.exe")
entry = fs.BuildPath(root, "launch.py")
If Not fs.FileExists(py) Then
  MsgBox "Missing runtime. Extract the complete HANGAR folder and try again.", 16, "ACBridge HANGAR"
  WScript.Quit 1
End If
If Not fs.FileExists(entry) Then
  MsgBox "Missing launch.py. Extract the complete HANGAR folder and try again.", 16, "ACBridge HANGAR"
  WScript.Quit 1
End If
sh.CurrentDirectory = root
sh.Run Chr(34) & py & Chr(34) & " -s " & Chr(34) & entry & Chr(34), 0, False
