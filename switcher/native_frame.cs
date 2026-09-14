// Extends the client area through Windows' resizing frame without a caption.
// Loaded once by the Windows WebView host; content remains the same HTML page.
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace SwitchWindow
{
    public sealed class Frame : NativeWindow
    {
        private bool taskbarVisible = true;
        [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr h, int n);
        [DllImport("user32.dll")] private static extern int SetWindowLong(IntPtr h, int n, int v);
        [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hgt, uint flags);

        public Frame(Form form)
        {
            AssignHandle(form.Handle);
            SetWindowLong(Handle, -16, (GetWindowLong(Handle, -16) | 0x000F0000) & ~0x00C00000);
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0, 0x0037);
        }

        public void SetTaskbarVisible(bool visible)
        {
            // Changing Form.ShowInTaskbar recreates its HWND and interrupts
            // WebView2 initialization. Change only the native extended style.
            taskbarVisible = visible;
            int style = GetWindowLong(Handle, -20);
            style = visible ? (style | 0x00040000) & ~0x00000080
                            : (style & ~0x00040000) | 0x00000080;
            SetWindowLong(Handle, -20, style);
        }

        protected override void WndProc(ref Message message)
        {
            if (!taskbarVisible && message.Msg == 0x007C && message.WParam.ToInt64() == -20)
            {
                // WinForms recomputes styles while changing initial opacity.
                // Keep its transparent startup form out of the taskbar throughout.
                int style = Marshal.ReadInt32(message.LParam, 4);
                Marshal.WriteInt32(message.LParam, 4, (style & ~0x00040000) | 0x00000080);
            }
            if (message.Msg == 0x0083) // WM_NCCALCSIZE
            {
                // lParam starts with the proposed window RECT in both forms.
                // No insets: all frame pixels belong to the WebView client area.
                message.Result = IntPtr.Zero;
                return;
            }
            base.WndProc(ref message);
        }
    }
}
