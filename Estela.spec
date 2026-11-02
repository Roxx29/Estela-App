# PyInstaller: carpeta dist\Estela\ con Estela.exe (onedir, sin consola, sin UPX)
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run.py"],
    datas=[("assets/icon.ico", "assets"), ("assets/remote.html", "assets"), ("extension", "extension")],  # extensión de Chrome: ver la cola
    # YouTube Music integrado: QtWebEngine se importa al abrir su ventana; necesita Network, Qml, Quick y OpenGL
    hiddenimports=collect_submodules("winrt") + ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    # QtQml/QtQuick: sus hooks copian la carpeta qml/ entera (decenas de MB) y WebEngine no usa ningún .qml;
    # las DLL Qt6Qml/Qt6Quick que sí necesita entran igual como dependencia binaria
    excludes=["tkinter", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDBus", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtMultimedia",
              "PySide6.QtPositioning", "PySide6.QtLocation"],
)
LOCALES = {"es.pak", "es-419.pak", "en-us.pak"}  # en-US es el respaldo obligatorio de Chromium


def _sobra(dest):
    d = dest.replace("\\", "/").lower()
    name = d.rsplit("/", 1)[-1]
    if "/qtwebengine_locales/" in d:
        return name not in LOCALES
    return (name.endswith(".qm")  # la app no carga traducciones de Qt
            or name == "qtwebengine_devtools_resources.pak"  # solo para el inspector F12
            or d.startswith("pyside6/qml/") or "/plugins/qmltooling/" in d)


a.binaries = [e for e in a.binaries if not _sobra(e[0])]
a.datas = [e for e in a.datas if not _sobra(e[0])]
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
