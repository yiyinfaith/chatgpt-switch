import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from switcher import __version__

parser = argparse.ArgumentParser()
parser.add_argument('--deps', type=Path)
parser.add_argument('--dist', type=Path, help='Stage a build before replacing a running EXE')
args = parser.parse_args()
root = Path(__file__).resolve().parent
workspace = tempfile.TemporaryDirectory(prefix='chatgpt-switch-build-')
work = Path(workspace.name)
cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
       '--windowed', '--name', 'ChatGPT Switch', '--distpath', str(args.dist or root),
       '--workpath', str(work / 'build'), '--specpath', str(work),
       '--add-data', str(root / 'ui') + os.pathsep + 'ui',
       '--add-data', str(root / 'switcher/native_frame.cs') + os.pathsep + 'switcher',
       '--add-data', str(root / 'switcher/native_tray.cs') + os.pathsep + 'switcher',
       '--add-data', str(root / 'switcher/apply_update.ps1') + os.pathsep + 'switcher']
for module in ('matplotlib', 'numpy', 'pandas', 'scipy', 'tkinter', 'playwright'):
    cmd += ['--exclude-module', module]
if sys.platform == 'win32':
    version_text = (root / 'assets/windows-version.txt').read_text('utf-8')
    version_parts = tuple(map(int, __version__.split('.'))) + (0,)
    import re
    version_text = re.sub(r'(filevers|prodvers)=\([^)]*\)', lambda m: m[1] + '=' + repr(version_parts), version_text)
    version_text = re.sub(r"(StringStruct\('(?:FileVersion|ProductVersion)', ')[^']+", lambda m: m[1] + __version__ + '.0', version_text)
    version_file = work / 'windows-version.txt'
    version_file.write_text(version_text, 'utf-8')
    cmd += ['--icon', str(root / 'assets/icon.ico'),
            '--version-file', str(version_file)]
    for module in ('pystray._win32', 'webview.platforms.winforms',
                   'webview.platforms.edgechromium', 'pythonnet', 'clr_loader'):
        cmd += ['--hidden-import', module]
    # Use WebView2 on Windows; don't accidentally bundle Anaconda's Qt browser.
    for module in ('webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
                   'webview.platforms.android', 'webview.platforms.cef', 'qtpy',
                   'PyQt5', 'PyQt6', 'PySide2', 'PySide6'):
        cmd += ['--exclude-module', module]
elif sys.platform == 'darwin':
    cmd += ['--hidden-import', 'pystray._darwin']
else:
    cmd += ['--hidden-import', 'pystray._xorg', '--hidden-import', 'pystray._appindicator']
env = os.environ.copy()
if args.deps:
    dependencies = str(args.deps.resolve())
    cmd += ['--paths', dependencies]
    # PyInstaller's collection hooks run in child interpreters too.
    env['PYTHONPATH'] = dependencies + os.pathsep + env.get('PYTHONPATH', '')
# Fail before PyInstaller can replace a working EXE with an incomplete one.
required = ['webview', 'pystray', 'PIL']
if sys.platform == 'win32':
    required += ['pythonnet', 'clr_loader', 'clr']
preflight = ('import importlib.util,sys; required=' + repr(required) +
             '; missing=[name for name in required if importlib.util.find_spec(name) is None]; '
             'sys.exit("Missing build dependencies: " + ", ".join(missing) + '
             '". Use build.py --deps <directory>." if missing else 0)')
# Also reject syntax errors before PyInstaller's module analysis can omit a file.
syntax_check = "import ast,pathlib; root=pathlib.Path.cwd(); files=[root/'main.py', *root.glob('switcher/*.py')]; [ast.parse(p.read_text(encoding='utf-8'),filename=str(p)) for p in files]"
try:
    subprocess.run([sys.executable, '-c', preflight], check=True, cwd=root, env=env)
    subprocess.run([sys.executable, '-c', syntax_check], check=True, cwd=root, env=env)
    subprocess.run(cmd + [str(root / 'main.py')], check=True, cwd=root, env=env)
finally:
    workspace.cleanup()
