import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows native menu")
class NativeTrayLayoutTests(unittest.TestCase):
    def test_real_menu_spacing_and_actions(self):
        framework = Path(os.environ["SystemRoot"]) / "Microsoft.NET"
        compiler = next((path for path in [
            framework / "Framework64/v4.0.30319/csc.exe",
            framework / "Framework/v4.0.30319/csc.exe",
        ] if path.is_file()), None)
        if compiler is None:
            self.skipTest(".NET Framework C# compiler unavailable")
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="chatgpt-switch-native-test-") as directory:
            executable = Path(directory) / "native-tray-test.exe"
            result = subprocess.run([
                str(compiler), "/nologo", "/target:exe", "/out:" + str(executable),
                "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                str(root / "switcher/native_tray.cs"), str(root / "tests/native_tray_layout.cs"),
            ], capture_output=True, text=True, errors="replace", timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable), str(root / "ui/icon.png")],
                capture_output=True, text=True, errors="replace", timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: symmetric menu bounds", result.stdout)


if __name__ == "__main__":
    unittest.main()
