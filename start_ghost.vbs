' KeyTone 幽灵版启动脚本（无窗口运行）
' 用法：双击本文件即可，运行后无窗口，启动成功弹自定义 toast 提示
Dim fso, ws, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

' 优先使用项目虚拟环境，否则退回系统 pythonw
If fso.FileExists(".venv\Scripts\pythonw.exe") Then
    pythonw = ".venv\Scripts\pythonw.exe"
Else
    pythonw = "pythonw.exe"
End If

' 第 2 个参数 0 = 隐藏窗口，第 3 个参数 False = 不等待
ws.Run """" & pythonw & """ main.py", 0, False
