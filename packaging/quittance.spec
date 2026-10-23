# -*- mode: python ; coding: utf-8 -*-
# Construction : pyinstaller packaging/quittance.spec --noconfirm   (depuis la racine du dépôt)
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src" / "quittance"

datas = [(str(SRC / "ui"), "quittance/ui"), (str(SRC / "assets"), "quittance/assets")]
binaries = []
hiddenimports = collect_submodules("keyring.backends") + ["msal_extensions"]
for pkg in ("pypdfium2", "pypdfium2_raw", "webview", "fpdf"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "run.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "aiosmtpd", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuittanceExpress",
    icon=str(ROOT / "packaging" / "icon.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="QuittanceExpress", upx=False)
