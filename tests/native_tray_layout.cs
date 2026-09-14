using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using SwitchWindow;

internal static class NativeTrayLayoutTest
{
    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static void CheckBounds(SoftMenu menu)
    {
        int inset = menu.S(12);
        Require(menu.Padding == new Padding(inset), "Menu padding changed during layout");
        Require(menu.Items[0].Bounds.Top == inset, "Top inset differs");
        int position = inset;
        foreach (ToolStripItem item in menu.Items)
        {
            Require(item.Bounds.Left == inset, "Left inset differs: " + item.Tag);
            Require(menu.ClientSize.Width - item.Bounds.Right == inset, "Right inset differs: " + item.Tag);
            Require(item.Bounds.Top == position, "Menu items overlap or wrap");
            Require(item.Bounds.Width > 0, "Menu item is clipped");
            position = item.Bounds.Bottom;
        }
        Require(menu.ClientSize.Height - position == inset, "Bottom inset differs");
    }

    private static void Pump(int milliseconds)
    {
        Stopwatch watch = Stopwatch.StartNew();
        do { Application.DoEvents(); System.Threading.Thread.Sleep(2); }
        while (watch.ElapsedMilliseconds < milliseconds);
    }

    private static void CheckHover(ToolStrip menu, SoftMenuItem item, ToolStripItem other)
    {
        Rectangle bounds = item.Bounds;
        item.SetHoverForTest(true); menu.Refresh();
        Require(item.HoverProgress < 1F, "Hover jumped directly to its final frame");
        Pump(45);
        Require(item.HoverProgress > 0F && item.HoverProgress < 1F, "Intermediate animation frame missing: " + item.HoverProgress + ", selected=" + item.Selected + ", visible=" + menu.Visible);
        Pump(300);
        Require(item.HoverProgress > 0.95F, "Hover did not settle: " + item.HoverProgress);
        Require(item.Bounds == bounds, "Animation moved the hit area");
        item.SetHoverForTest(false); ((SoftMenuItem)other).SetHoverForTest(true); menu.Refresh();
        Pump(45);
        Require(item.HoverProgress > 0F && item.HoverProgress < 1F, "Hover exit did not animate");
        // Reverse before the exit completes: the current frame must be retained.
        float before = item.HoverProgress;
        item.SetHoverForTest(true); menu.Refresh();
        Require(item.HoverProgress > 0F && item.HoverProgress <= before,
            "Rapid re-entry restarted at an endpoint: " + before + " -> " + item.HoverProgress);
        Pump(300);
        item.SetHoverForTest(false); ((SoftMenuItem)other).SetHoverForTest(true); menu.Refresh(); Pump(240);
        Require(item.HoverProgress < 0.05F, "Hover failed to return to rest: " + item.HoverProgress);
        Require(item.Bounds == bounds, "Exit animation moved the hit area");
    }

    [STAThread]
    private static int Main(string[] arguments)
    {
        try
        {
            Application.EnableVisualStyles();
            foreach (float scale in new float[] { 1F, 1.25F, 1.5F, 2F })
            {
                Console.WriteLine("DPI: " + scale);
                using (SoftMenu menu = new SoftMenu(scale))
                {
                    SoftMenuItem entry = new SoftMenuItem("Spacing test");
                    entry.AutoSize = false;
                    entry.Size = new Size(menu.ContentWidth, menu.S(44));
                    menu.Items.Add(entry);
                    SoftMenuItem other = new SoftMenuItem("Other");
                    other.AutoSize = false; other.Size = entry.Size;
                    menu.Items.Add(other);
                    menu.AutoSize = false;
                    menu.Size = new Size(menu.S(292), entry.Height * 2 + menu.Padding.Vertical);
                    menu.PerformLayout();
                    CheckBounds(menu);
                    menu.Show(new Point(100, 100)); menu.Refresh();
                    CheckHover(menu, entry, other);
                    entry.Select(); menu.Refresh(); Pump(40);
                    entry.Enabled = false;
                    Require(entry.HoverProgress == 0F, "Disabled item retained hover");
                    menu.Close(); Pump(30);
                    Require(other.HoverProgress == 0F, "Hidden menu retained hover");
                }
            }
            using (Form owner = new Form())
            {
                owner.CreateControl();
                List<string> dispatched = new List<string>();
                using (NativeTray tray = new NativeTray(owner, arguments[0], dispatched.Add))
                {
                    SoftMenu menu = (SoftMenu)typeof(NativeTray).GetField("menu", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(tray);
                    Require(menu.Items.Count == 9, "Menu entries changed");
                    menu.PerformLayout();
                    CheckBounds(menu);
                    tray.Update("thirdparty", false, "2026-09-12 06:00:00");
                    Require(((ToolStripMenuItem)menu.Items[2]).Checked, "Active mode missing");
                    tray.Update("account", true, "2026-09-12 06:00:01");
                    Require(!menu.Items[1].Enabled && !menu.Items[2].Enabled && !menu.Items[8].Enabled, "Busy action guard lost");
                    tray.Update("thirdparty", false, "2026-09-12 06:00:02");
                    for (int cycle = 0; cycle < 3; cycle++)
                    {
                        menu.Show(new Point(100 + cycle * 20, 100));
                        Application.DoEvents();
                        CheckBounds(menu);
                        menu.Close();
                    }
                    dispatched.Clear();
                    foreach (ToolStripItem item in menu.Items)
                    {
                        if (item.Enabled && item is ToolStripMenuItem) item.PerformClick();
                    }
                    Require(String.Join("|", dispatched) == "account|thirdparty|settings|main|config|quit", "Action routing changed");
                    foreach (ToolStripItem item in menu.Items)
                        if (item.Enabled && item is ToolStripMenuItem)
                            Require(item is SoftMenuItem, "Top-level action has no animation: " + item.Tag);
                    tray.Update("thirdparty", false, "2026-09-12 06:00:03", "*first~First API|second~Second API");
                    SoftMenuItem parent = (SoftMenuItem)menu.Items[2];
                    menu.Show(new Point(100, 100)); parent.Select(); menu.Refresh(); Pump(240);
                    parent.ShowDropDown(); Pump(30);
                    foreach (ToolStripItem item in parent.DropDownItems)
                        Require(item.Bounds.Left == parent.DropDown.ClientSize.Width - item.Bounds.Right,
                            "Submenu horizontal insets differ: " + item.Bounds + " in " + parent.DropDown.ClientSize);
                    SoftMenuItem child = parent.DropDownItems[0] as SoftMenuItem;
                    Require(child != null && child.Checked, "Active submenu action has no animation or badge");
                    CheckHover(parent.DropDown, child, parent.DropDownItems[1]);
                    Require(parent.HoverProgress > 0.95F, "Parent lost hover while its submenu was open");
                    dispatched.Clear(); child.PerformClick();
                    Require(dispatched.Contains("profile:first"), "Submenu action routing changed");
                    parent.ShowDropDown(); child.Select(); parent.DropDown.Refresh(); Pump(40);
                    bool childDisposed = false;
                    child.Disposed += delegate { childDisposed = true; };
                    tray.Update("account", false, "2026-09-12 06:00:04", "replacement~Replacement API");
        Pump(300);
                    Require(childDisposed && child.Owner == null && child.HoverProgress == 0F,
                        "Replaced animated profile was not released: " + childDisposed + ", owner=" + child.Owner + ", hover=" + child.HoverProgress);
                    menu.Close(); Pump(30);
                    Require(parent.HoverProgress < 0.05F, "Closing did not reset the parent animation");
                    Console.WriteLine("PASS: symmetric menu bounds, repeated opening, active mode, busy guard, six actions; hover enter/exit/reversal at four DPI scales, submenu hover/routing/replacement, hidden and disabled reset");
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
