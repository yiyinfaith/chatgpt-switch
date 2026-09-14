// Native notification-area integration with a menu drawn in the app's palette.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace SwitchWindow
{
    // Animate painting only: fixed hit areas keep hover and submenu navigation
    // stable even when the pointer sits on an item's edge.
    internal sealed class SoftMenuItem : ToolStripMenuItem
    {
        private readonly Timer animation = new Timer();
        private readonly Stopwatch elapsed = new Stopwatch();
        private ToolStrip observedOwner;
        private float progress, origin, target;
        public float HoverProgress { get { return progress; } }

        public SoftMenuItem(string text) : base(text)
        {
            animation.Interval = 15;
            animation.Tick += delegate
            {
                if (Owner == null || !Owner.Visible || !Enabled) { ResetHover(); return; }
                AdvanceHover();
                Invalidate();
            };
        }
        private void AdvanceHover()
        {
            if (!animation.Enabled) return;
            float time = Math.Min(1F, (float)elapsed.Elapsed.TotalMilliseconds / 200F);
            float remaining = 1F - time;
            progress = origin + (target - origin) * (1F - remaining * remaining * remaining);
            if (time >= 1F) { progress = target; animation.Stop(); elapsed.Reset(); }
        }
        private void ResetHover()
        {
            animation.Stop(); elapsed.Reset();
            progress = origin = target = 0F;
            Invalidate();
        }
        private void SetHoverTarget(float next)
        {
            AdvanceHover();
            if (next == target) return;
            origin = progress;
            target = next;
            elapsed.Restart();
            animation.Start();
            Invalidate();
        }
        internal void SetHoverForTest(bool entered)
        {
            if (Enabled) SetHoverTarget(entered ? 1F : 0F);
            else ResetHover();
        }
        private void OwnerVisibilityChanged(object sender, EventArgs e)
        {
            if (observedOwner == null || !observedOwner.Visible) ResetHover();
        }
        protected override void OnOwnerChanged(EventArgs e)
        {
            if (observedOwner != null) observedOwner.VisibleChanged -= OwnerVisibilityChanged;
            base.OnOwnerChanged(e);
            observedOwner = Owner;
            if (observedOwner != null) observedOwner.VisibleChanged += OwnerVisibilityChanged;
            ResetHover();
        }
        protected override void OnEnabledChanged(EventArgs e)
        {
            base.OnEnabledChanged(e);
            if (!Enabled) ResetHover();
        }
        protected override void OnMouseEnter(EventArgs e)
        {
            base.OnMouseEnter(e);
            if (Enabled) SetHoverTarget(1F);
        }
        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            if (Enabled && !(HasDropDownItems && DropDown.Visible)) SetHoverTarget(0F);
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            AdvanceHover();
            // An open submenu keeps its parent raised while the pointer moves
            // onto a child. Painting itself never changes the animation target.
            if (Enabled && HasDropDownItems && DropDown.Visible) SetHoverTarget(1F);
            base.OnPaint(e);
        }
        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                if (observedOwner != null) observedOwner.VisibleChanged -= OwnerVisibilityChanged;
                observedOwner = null;
                animation.Stop(); animation.Dispose(); elapsed.Stop();
            }
            base.Dispose(disposing);
        }
    }

    internal sealed class SoftMenu : ToolStripDropDown
    {
        public readonly float DpiScale;
        public string Status = "本地运行，随时切换";
        public string WestTime = "美西时间 · 正在读取";
        public SoftMenu(float scale)
        {
            DpiScale = scale;
            // Keep item spacing unchanged while tightening the outer frame.
            Padding = new Padding(S(12));
            LayoutStyle = ToolStripLayoutStyle.Flow;
            FlowLayoutSettings layout = (FlowLayoutSettings)LayoutSettings;
            layout.FlowDirection = FlowDirection.TopDown;
            layout.WrapContents = false;
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.FromArgb(246, 248, 255);
            Renderer = new SoftRenderer(this);
            DropShadowEnabled = true;
        }
        public int ContentWidth { get { return S(292) - Padding.Horizontal; } }
        public int S(float value) { return (int)Math.Round(value * DpiScale); }
        protected override void OnSizeChanged(EventArgs e)
        {
            base.OnSizeChanged(e);
            if (Width > 0 && Height > 0)
            {
                Region old = Region;
                using (GraphicsPath path = SoftRenderer.Round(new Rectangle(0, 0, Width, Height), Math.Max(1, S(16))))
                    Region = new Region(path);
                if (old != null) old.Dispose();
            }
        }
    }

    internal sealed class SoftRenderer : ToolStripProfessionalRenderer
    {
        private readonly SoftMenu menu;
        private readonly Color ink = Color.FromArgb(62, 76, 105);
        private readonly Color muted = Color.FromArgb(143, 154, 177);
        public SoftRenderer(SoftMenu menu) { this.menu = menu; RoundedEdges = false; }
        public static GraphicsPath Round(Rectangle r, int radius)
        {
            GraphicsPath path = new GraphicsPath();
            int d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
            path.AddArc(r.Left, r.Top, d, d, 180, 90);
            path.AddArc(r.Right - d, r.Top, d, d, 270, 90);
            path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            path.AddArc(r.Left, r.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
        protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle r = new Rectangle(0, 0, e.ToolStrip.Width, e.ToolStrip.Height);
            using (LinearGradientBrush brush = new LinearGradientBrush(r, Color.FromArgb(246, 244, 255), Color.FromArgb(236, 247, 252), 55F))
                e.Graphics.FillRectangle(brush, r);
        }
        protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e)
        {
            Rectangle r = new Rectangle(0, 0, e.ToolStrip.Width - 1, e.ToolStrip.Height - 1);
            using (GraphicsPath path = Round(r, menu.S(16)))
            using (Pen pen = new Pen(Color.FromArgb(225, 230, 247)))
                e.Graphics.DrawPath(pen, path);
        }
        protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e)
        {
            float hover = Hover(e.Item);
            if (!e.Item.Enabled || hover <= 0F) return;
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle r = new Rectangle(menu.S(2), menu.S(2) - Lift(e.Item), e.Item.Width - menu.S(4), e.Item.Height - menu.S(4));
            bool exit = (string)e.Item.Tag == "quit";
            Rectangle shadow = r;
            shadow.Offset(0, menu.S(2));
            using (GraphicsPath path = Round(shadow, menu.S(10)))
            using (SolidBrush brush = new SolidBrush(Color.FromArgb((int)(22 * hover), 102, 121, 165)))
                e.Graphics.FillPath(brush, path);
            using (GraphicsPath path = Round(r, menu.S(10)))
            using (LinearGradientBrush brush = new LinearGradientBrush(r,
                Color.FromArgb((int)(255 * hover), exit ? Color.FromArgb(255, 224, 236) : Color.FromArgb(210, 214, 255)),
                Color.FromArgb((int)(255 * hover), exit ? Color.FromArgb(255, 239, 246) : Color.FromArgb(218, 242, 255)), 20F))
                e.Graphics.FillPath(brush, path);
            using (GraphicsPath path = Round(r, menu.S(10)))
            using (Pen pen = new Pen(Color.FromArgb((int)(255 * hover), exit ? Color.FromArgb(224, 177, 194) : Color.FromArgb(167, 174, 235)), Math.Max(1, menu.S(1))))
                e.Graphics.DrawPath(pen, path);
        }
        private static float Hover(ToolStripItem item)
        {
            SoftMenuItem animated = item as SoftMenuItem;
            return animated == null ? 0F : animated.HoverProgress;
        }
        private int Lift(ToolStripItem item) { return menu.S(2F * Hover(item)); }
        protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e)
        {
            string action = e.Item.Tag as string;
            if (action == "header")
            {
                using (Font small = new Font(menu.Font.FontFamily, 6.5F, FontStyle.Bold))
                using (Font title = new Font(menu.Font.FontFamily, 11F, FontStyle.Bold))
                using (Font label = new Font(menu.Font.FontFamily, 9F, FontStyle.Bold))
                using (Font time = new Font(menu.Font.FontFamily, 11F, FontStyle.Bold))
                {
                    TextRenderer.DrawText(e.Graphics, "A LITTLE SWITCH", small, new Point(menu.S(12), menu.S(8)), muted);
                    TextRenderer.DrawText(e.Graphics, "ChatGPT Switch", title, new Point(menu.S(11), menu.S(25)), ink);
                    TextRenderer.DrawText(e.Graphics, "美西时间", label, new Point(menu.S(12), menu.S(51)), muted);
                    TextRenderer.DrawText(e.Graphics, menu.WestTime, time, new Point(menu.S(12), menu.S(68)), muted);
                }
                return;
            }
            int lift = Lift(e.Item);
            DrawGlyph(e.Graphics, action, e.Item.Enabled, lift);
            Color color = !e.Item.Enabled ? muted : action == "quit" ? Color.FromArgb(168, 112, 133) : ink;
            Rectangle text = new Rectangle(menu.S(49), -lift, e.Item.Width - menu.S(101), e.Item.Height);
            TextRenderer.DrawText(e.Graphics, e.Text, menu.Font, text, color,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix);
            ToolStripMenuItem item = e.Item as ToolStripMenuItem;
            if (item != null && item.Checked)
            {
                Rectangle badge = new Rectangle(e.Item.Width - menu.S(50), menu.S(12) - lift, menu.S(41), menu.S(21));
                using (GraphicsPath path = Round(badge, menu.S(7)))
                using (SolidBrush brush = new SolidBrush(Color.FromArgb(225, 242, 232)))
                    e.Graphics.FillPath(brush, path);
                using (Font font = new Font(menu.Font.FontFamily, 8F, FontStyle.Bold))
                    TextRenderer.DrawText(e.Graphics, "当前", font, badge, Color.FromArgb(61, 111, 83),
                        TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine);
            }
        }
        private void DrawGlyph(Graphics graphics, string action, bool enabled, int lift)
        {
            GraphicsState saved = graphics.Save();
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.TranslateTransform(menu.S(11), menu.S(8) - lift);
            graphics.ScaleTransform(menu.DpiScale, menu.DpiScale);
            using (GraphicsPath path = Round(new Rectangle(0, 0, 28, 28), 8))
            using (SolidBrush background = new SolidBrush(action == "quit" ? Color.FromArgb(245, 233, 239) : Color.FromArgb(234, 233, 250)))
                graphics.FillPath(background, path);
            using (Pen pen = new Pen(!enabled ? muted : action == "quit" ? Color.FromArgb(184, 135, 153) : Color.FromArgb(137, 140, 195), 1.4F))
            {
                pen.StartCap = LineCap.Round; pen.EndCap = LineCap.Round; pen.LineJoin = LineJoin.Round;
                if (action == "account")
                {
                    graphics.DrawEllipse(pen, 11, 6, 6, 6);
                    graphics.DrawArc(pen, 7, 13, 14, 14, 180, 180);
                    graphics.DrawLine(pen, 7, 20, 7, 22); graphics.DrawLine(pen, 21, 20, 21, 22);
                }
                else if (action == "thirdparty")
                {
                    graphics.DrawLines(pen, new Point[] { new Point(10, 9), new Point(5, 14), new Point(10, 19) });
                    graphics.DrawLines(pen, new Point[] { new Point(18, 9), new Point(23, 14), new Point(18, 19) });
                    graphics.DrawLine(pen, 15, 7, 12, 21);
                }
                else if (action == "settings")
                {
                    graphics.DrawLine(pen, 6, 10, 22, 10); graphics.DrawLine(pen, 6, 18, 22, 18);
                    graphics.DrawEllipse(pen, 9, 7, 5, 5); graphics.DrawEllipse(pen, 16, 15, 5, 5);
                }
                else if (action == "main")
                {
                    graphics.DrawRectangle(pen, 6, 7, 16, 14); graphics.DrawLine(pen, 6, 11, 22, 11);
                    graphics.DrawLine(pen, 10, 11, 10, 21);
                }
                else if (action == "config")
                {
                    graphics.DrawRectangle(pen, 7, 5, 14, 18);
                    graphics.DrawLine(pen, 10, 10, 18, 10);
                    graphics.DrawLine(pen, 10, 14, 18, 14);
                    graphics.DrawLine(pen, 10, 18, 16, 18);
                }
                else if (action == "quit")
                {
                    graphics.DrawLine(pen, 9, 9, 19, 19); graphics.DrawLine(pen, 19, 9, 9, 19);
                }
            }
            graphics.Restore(saved);
        }
        protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e)
        {
            using (Pen pen = new Pen(Color.FromArgb(223, 229, 244)))
                e.Graphics.DrawLine(pen, menu.S(12), e.Item.Height / 2, e.Item.Width - menu.S(12), e.Item.Height / 2);
        }
        protected override void OnRenderItemCheck(ToolStripItemImageRenderEventArgs e) { }
        protected override void OnRenderArrow(ToolStripArrowRenderEventArgs e)
        {
            Rectangle arrow = e.ArrowRectangle;
            arrow.Offset(0, -Lift(e.Item));
            e.ArrowRectangle = arrow;
            base.OnRenderArrow(e);
        }
    }

    public sealed class NativeTray : IDisposable
    {
        [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr handle);
        [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr handle);
        private readonly NotifyIcon icon;
        private readonly SoftMenu menu;
        private readonly Icon artwork;
        private readonly Action<string> dispatchAction;
        private readonly Dictionary<string, ToolStripMenuItem> actions = new Dictionary<string, ToolStripMenuItem>();
        private readonly ToolStripMenuItem profileMenu;
        private string profileSignature;
        public NativeTray(Form owner, string imagePath, Action<string> dispatch)
        {
            dispatchAction = dispatch;
            float scale;
            using (Graphics graphics = owner.CreateGraphics()) scale = graphics.DpiX / 96F;
            menu = new SoftMenu(scale);
            ToolStripMenuItem header = new ToolStripMenuItem("ChatGPT Switch");
            header.Tag = "header"; header.Enabled = false; header.AutoSize = false;
            header.Size = new Size(menu.ContentWidth, menu.S(94));
            menu.Items.Add(header);
            Add("account", "使用账号额度", dispatch);
            profileMenu = Add("thirdparty", "使用第三方api", dispatch);
            profileMenu.DropDownDirection = ToolStripDropDownDirection.Right;
            profileMenu.DropDown.AutoSize = false;
            profileMenu.DropDown.Padding = new Padding(menu.S(12));
            profileMenu.DropDown.Renderer = menu.Renderer;
            profileMenu.DropDown.Resize += delegate
            {
                if (profileMenu.DropDown.Width > 0 && profileMenu.DropDown.Height > 0)
                {
                    Region old = profileMenu.DropDown.Region;
                    using (GraphicsPath path = SoftRenderer.Round(
                        new Rectangle(0, 0, profileMenu.DropDown.Width - 1, profileMenu.DropDown.Height - 1), menu.S(14)))
                        profileMenu.DropDown.Region = new Region(path);
                    if (old != null) old.Dispose();
                }
            };
            profileMenu.DropDownOpening += delegate { profileMenu.DropDownDirection = ToolStripDropDownDirection.Right; };
            Separator();
            Add("settings", "打开设置", dispatch);
            Add("main", "打开主面板", dispatch);
            Add("config", "打开 config.toml", dispatch);
            Separator();
            Add("quit", "退出程序", dispatch);
            menu.AutoSize = false;
            int contentHeight = 0;
            foreach (ToolStripItem item in menu.Items) contentHeight += item.Height;
            menu.Size = new Size(menu.S(292), contentHeight + menu.S(12) * 2);
            using (Bitmap bitmap = new Bitmap(imagePath))
            {
                IntPtr handle = bitmap.GetHicon();
                try { using (Icon temporary = Icon.FromHandle(handle)) artwork = (Icon)temporary.Clone(); }
                finally { DestroyIcon(handle); }
            }
            icon = new NotifyIcon();
            icon.Icon = artwork;
            icon.Text = "ChatGPT Switch";
            icon.MouseClick += delegate(object sender, MouseEventArgs e)
            {
                if (e.Button == MouseButtons.Left) dispatch("main");
                if (e.Button == MouseButtons.Right)
                {
                    SetForegroundWindow(new IntPtr(NotificationHandle));
                    ShowNearTray(Cursor.Position);
                    SetForegroundWindow(menu.Handle);
                    menu.Focus();
                }
            };
            menu.Opening += delegate { dispatch("refresh"); };
            icon.Visible = true;
        }
        private void ShowNearTray(Point trayPoint)
        {
            Rectangle workArea = Screen.GetWorkingArea(trayPoint);
            int gap = menu.S(8);
            int inset = menu.S(6);
            int rightTop = trayPoint.X + gap;
            int leftTop = trayPoint.X - menu.Width - gap;
            int top = trayPoint.Y - menu.Height - gap;
            int bottom = trayPoint.Y + gap;

            // Try quadrants in the requested order.  Unlike the stock
            // NotifyIcon menu (which is centered above the icon), this keeps
            // the menu visibly offset from the icon and only changes sides
            // when the current quadrant cannot fit in the working area.
            Point[] candidates = new Point[] {
                new Point(rightTop, top),
                new Point(leftTop, top),
                new Point(rightTop, bottom),
                new Point(leftTop, bottom)
            };
            foreach (Point candidate in candidates)
            {
                if (candidate.X >= workArea.Left + inset &&
                    candidate.Y >= workArea.Top + inset &&
                    candidate.X + menu.Width <= workArea.Right - inset &&
                    candidate.Y + menu.Height <= workArea.Bottom - inset)
                {
                    menu.Show(candidate);
                    return;
                }
            }

            // Extremely small work areas can reject every quadrant.  Keep a
            // deterministic, visible fallback based on the primary (right-up)
            // position rather than allowing the menu off-screen.
            int x = Math.Max(workArea.Left + inset, Math.Min(rightTop, workArea.Right - menu.Width - inset));
            int y = Math.Max(workArea.Top + inset, Math.Min(top, workArea.Bottom - menu.Height - inset));
            menu.Show(new Point(x, y));
        }
        private ToolStripMenuItem Add(string action, string text, Action<string> dispatch)
        {
            ToolStripMenuItem item = new SoftMenuItem(text);
            item.Tag = action; item.Name = action; item.AutoSize = false;
            item.Size = new Size(menu.ContentWidth, menu.S(44));
            item.Click += delegate { dispatch(action); };
            actions.Add(action, item); menu.Items.Add(item);
            return item;
        }
        private void ClearProfiles()
        {
            while (profileMenu.DropDownItems.Count > 0)
            {
                ToolStripItem old = profileMenu.DropDownItems[0];
                profileMenu.DropDownItems.RemoveAt(0);
                old.Dispose();
            }
        }
        // The compact wire format is id~display-name|id~display-name. Both
        // fields are URI escaped by Python, so profile names remain lossless.
        private void SetProfiles(string encoded, Action<string> dispatch)
        {
            if (profileSignature == encoded) return;
            profileSignature = encoded;
            ClearProfiles();
            if (String.IsNullOrEmpty(encoded))
            {
                ToolStripMenuItem empty = new ToolStripMenuItem("暂无 API 配置");
                empty.Enabled = false;
                empty.AutoSize = false; empty.Size = new Size(menu.ContentWidth, menu.S(38));
                profileMenu.DropDownItems.Add(empty);
                profileMenu.DropDown.Size = new Size(menu.ContentWidth, menu.S(38) + profileMenu.DropDown.Padding.Vertical);
                return;
            }
            foreach (string record in encoded.Split(new char[] { '|' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string[] fields = record.Split(new char[] { '~' }, 2);
                if (fields.Length != 2) continue;
                string id, name;
                bool selected = fields[0].StartsWith("*", StringComparison.Ordinal);
                try { id = Uri.UnescapeDataString(selected ? fields[0].Substring(1) : fields[0]); name = Uri.UnescapeDataString(fields[1]); }
                catch { continue; }
                if (String.IsNullOrEmpty(id)) continue;
                ToolStripMenuItem child = new SoftMenuItem(String.IsNullOrEmpty(name) ? "未命名 API" : name);
                string profileId = id;
                child.Tag = "profile:" + profileId; child.AutoSize = false;
                child.Checked = selected;
                child.Size = new Size(menu.ContentWidth, menu.S(40));
                child.Click += delegate { dispatch("profile:" + profileId); };
                profileMenu.DropDownItems.Add(child);
            }
            if (profileMenu.DropDownItems.Count == 0)
            {
                ToolStripMenuItem empty = new ToolStripMenuItem("暂无 API 配置");
                empty.Enabled = false; empty.AutoSize = false; empty.Size = new Size(menu.ContentWidth, menu.S(38));
                profileMenu.DropDownItems.Add(empty);
            }
            int height = 0;
            foreach (ToolStripItem item in profileMenu.DropDownItems) height += item.Height;
            // Child items already contain their own horizontal inset. Match
            // the popup to their width instead of adding the main menu's padding.
            profileMenu.DropDown.Size = new Size(menu.ContentWidth, height + profileMenu.DropDown.Padding.Vertical);
        }
        private void Separator()
        {
            ToolStripSeparator separator = new ToolStripSeparator();
            separator.AutoSize = false; separator.Size = new Size(menu.ContentWidth, menu.S(12));
            menu.Items.Add(separator);
        }
        public void Update(string mode, bool busy, string westTime)
        { Update(mode, busy, westTime, ""); }
        public void Update(string mode, bool busy, string westTime, string profiles)
        {
            actions["account"].Checked = mode == "account";
            actions["thirdparty"].Checked = mode == "thirdparty";
            actions["account"].Enabled = actions["thirdparty"].Enabled = actions["quit"].Enabled = !busy;
            profileMenu.Enabled = !busy;
            menu.Status = busy ? "正在处理，请稍候…" : mode == "account" ? "当前 · 账号额度" : mode == "thirdparty" ? "当前 · 第三方 API" : "本地运行，随时切换";
            menu.WestTime = westTime;
            // Keep the submenu current without changing the six top-level
            // actions or their layout bounds.
            SetProfiles(profiles, dispatchAction);
            icon.Text = "ChatGPT Switch · 美西 " + westTime;
            menu.Invalidate();
        }
        public void Notify(string message)
        {
            icon.ShowBalloonTip(3500, "ChatGPT Switch", message, ToolTipIcon.Info);
        }
        // Read-only handles let isolated integration tests exercise the actual
        // notification window and menu via Windows messages and mouse input.
        public long NotificationHandle
        {
            get
            {
                FieldInfo field = typeof(NotifyIcon).GetField("window", BindingFlags.Instance | BindingFlags.NonPublic);
                NativeWindow window = field == null ? null : field.GetValue(icon) as NativeWindow;
                return window == null ? 0 : window.Handle.ToInt64();
            }
        }
        public bool IconVisible { get { return icon.Visible; } }
        public bool MenuVisible { get { return menu.Visible; } }
        public long MenuHandle { get { return menu.IsHandleCreated ? menu.Handle.ToInt64() : 0; } }
        public int[] MenuInsets
        {
            get
            {
                Rectangle first = menu.Items[0].Bounds;
                Rectangle last = menu.Items[menu.Items.Count - 1].Bounds;
                return new int[] { first.Left, first.Top, menu.ClientSize.Width - first.Right, menu.ClientSize.Height - last.Bottom };
            }
        }
        public string MenuItems { get { return String.Join("|", new string[] { actions["account"].Text, actions["thirdparty"].Text, actions["settings"].Text, actions["main"].Text, actions["config"].Text, actions["quit"].Text }); } }
        public string ProfileItems
        {
            get
            {
                List<string> values = new List<string>();
                foreach (ToolStripItem item in profileMenu.DropDownItems) values.Add(item.Text);
                return String.Join("|", values.ToArray());
            }
        }
        public int[] ItemBounds(string action)
        {
            ToolStripMenuItem item = actions[action];
            Point origin = menu.PointToScreen(item.Bounds.Location);
            return new int[] { origin.X, origin.Y, item.Width, item.Height };
        }
        public void Dispose()
        {
            icon.Visible = false;
            icon.Dispose();
            menu.Dispose();
            artwork.Dispose();
        }
    }
}
