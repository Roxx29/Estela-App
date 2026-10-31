# PyInstaller: carpeta dist\Estela\ con Estela.exe (onedir, sin consola, sin UPX)
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run.py"],
    datas=[("assets/icon.ico", "assets"), ("extension", "extension")],  # extensión de Chrome: ver la cola
    # YouTube Music integrado: QtWebEngine se importa al abrir su ventana; necesita Network, Qml, Quick y OpenGL
    hiddenimports=collect_submodules("winrt") + ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    excludes=["tkinter", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDBus"],
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
