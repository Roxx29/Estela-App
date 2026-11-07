# PyInstaller: carpeta dist\Estela\ con Estela.exe (onedir, sin consola, sin UPX)
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run.py"],
    datas=[("assets/icon.ico", "assets")],  # solo el paquete brillomusical: server/ (Node) no entra
    hiddenimports=collect_submodules("winrt"),
    excludes=["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL", "PySide6.QtDBus"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Estela",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Estela", upx=False)
