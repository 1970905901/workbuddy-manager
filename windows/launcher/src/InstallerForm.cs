using System;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

namespace WorkBuddyLauncher
{
    /// 安装向导：欢迎 → 选择位置 → 安装进度 → 完成。
    internal sealed class InstallerForm : WizardForm
    {
        private readonly Panel _pageWelcome;
        private readonly Panel _pageLocation;
        private readonly Panel _pageProgress;
        private readonly Panel _pageDone;

        private TextBox _txtDir;
        private CheckBox _chkDesktop;
        private CheckBox _chkStartMenu;
        private Label _lblSpace;
        private ProgressBar _progress;
        private Label _lblStatus;
        private CheckBox _chkLaunch;
        private Label _lblDoneDetail;

        private int _pageIndex;
        private string _installTarget;

        public InstallerForm()
            : base("安装 " + LauncherPaths.AppName, "WorkBuddy Manager 安装向导 · 内置全部运行环境")
        {
            _pageWelcome = MakePage();
            _pageLocation = MakePage();
            _pageProgress = MakePage();
            _pageDone = MakePage();

            BuildWelcomePage();
            BuildLocationPage();
            BuildProgressPage();
            BuildDonePage();

            ShowPage(_pageWelcome);
            UpdateNav();
        }

        private void BuildWelcomePage()
        {
            MakePageTitle(_pageWelcome, "欢迎使用 " + LauncherPaths.AppName + " 安装向导", 32);
            MakePageText(_pageWelcome,
                "本向导将指导您完成 " + LauncherPaths.AppName + " 的安装过程。\n\n" +
                "安装包已内置运行所需的全部环境（Python 运行时、上游网关、前端页面），\n" +
                "安装完成后即可直接启动，无需再安装任何依赖。",
                84, 90);
            MakePageText(_pageWelcome,
                "建议在继续之前关闭其他正在运行的 WorkBuddy 程序。\n单击「下一步」继续。",
                184, 50).ForeColor = SystemColors.GrayText;
        }

        private void BuildLocationPage()
        {
            MakePageTitle(_pageLocation, "选择安装位置", 28);
            MakePageText(_pageLocation,
                "安装向导将把程序安装到以下文件夹。若要安装到其他位置，请单击「浏览」。",
                66, 24);

            var dirLabel = new Label();
            dirLabel.Text = "安装到(&I):";
            dirLabel.SetBounds(28, 124, 76, 22);
            _pageLocation.Controls.Add(dirLabel);

            _txtDir = new TextBox();
            _txtDir.SetBounds(108, 120, 296, 24);
            _txtDir.Text = LauncherPaths.DefaultInstallDir;
            _pageLocation.Controls.Add(_txtDir);

            var btnBrowse = new Button();
            btnBrowse.Text = "浏览…";
            btnBrowse.SetBounds(412, 119, 64, 26);
            btnBrowse.Click += OnBrowseClick;
            _pageLocation.Controls.Add(btnBrowse);

            _chkDesktop = new CheckBox();
            _chkDesktop.Text = "创建桌面快捷方式";
            _chkDesktop.SetBounds(108, 158, 160, 22);
            _chkDesktop.Checked = true;
            _pageLocation.Controls.Add(_chkDesktop);

            _chkStartMenu = new CheckBox();
            _chkStartMenu.Text = "创建开始菜单快捷方式";
            _chkStartMenu.SetBounds(276, 158, 190, 22);
            _chkStartMenu.Checked = true;
            _pageLocation.Controls.Add(_chkStartMenu);

            _lblSpace = MakePageText(_pageLocation, string.Empty, 198, 40);
            _lblSpace.ForeColor = SystemColors.GrayText;
        }

        private void BuildProgressPage()
        {
            MakePageTitle(_pageProgress, "正在安装 " + LauncherPaths.AppName, 28);
            MakePageText(_pageProgress, "正在复制文件，请稍候…", 66, 24);

            _progress = new ProgressBar();
            _progress.SetBounds(28, 116, ContentWidth - 56, 22);
            _pageProgress.Controls.Add(_progress);

            _lblStatus = MakePageText(_pageProgress, string.Empty, 150, 60);
            _lblStatus.AutoEllipsis = true;
        }

        private void BuildDonePage()
        {
            MakePageTitle(_pageDone, LauncherPaths.AppName + " 安装完成", 36);
            _lblDoneDetail = MakePageText(_pageDone, string.Empty, 84, 60);

            _chkLaunch = new CheckBox();
            _chkLaunch.Text = "立即启动 " + LauncherPaths.AppName;
            _chkLaunch.SetBounds(28, 158, 300, 24);
            _chkLaunch.Checked = true;
            _pageDone.Controls.Add(_chkLaunch);
        }

        private void UpdateNav()
        {
            // 进度页（2）安装中不可操作：隐藏全部导航
            BackButton.Visible = _pageIndex == 1;
            NextButton.Visible = _pageIndex != 2;
            BtnCancel.Visible = _pageIndex != 2;
            NextButton.Text = _pageIndex == 3 ? "完成(&F)" : "下一步(&N) >";
            if (_pageIndex == 1) { UpdateSpaceInfo(); }
        }

        protected override void OnBack()
        {
            if (_pageIndex != 1) { return; }
            _pageIndex = 0;
            ShowPage(_pageWelcome);
            UpdateNav();
        }

        protected override void OnNext()
        {
            switch (_pageIndex)
            {
                case 0:
                    _pageIndex = 1;
                    ShowPage(_pageLocation);
                    UpdateNav();
                    break;
                case 1:
                    if (ValidateLocation()) { StartInstall(); }
                    break;
                case 3:
                    FinishAndMaybeLaunch();
                    break;
            }
        }

        private void OnBrowseClick(object sender, EventArgs e)
        {
            using (var dialog = new FolderBrowserDialog())
            {
                dialog.Description = "选择安装目录";
                dialog.SelectedPath = _txtDir.Text.Trim();
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    _txtDir.Text = dialog.SelectedPath;
                }
            }
        }

        private bool ValidateLocation()
        {
            string raw = _txtDir.Text.Trim();
            if (raw.Length == 0)
            {
                MessageBox.Show(this, "请先选择安装目录。", "安装", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }

            string target;
            try
            {
                target = Path.GetFullPath(raw);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "目录路径无效：" + ex.Message, "安装", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }

            if (File.Exists(target))
            {
                MessageBox.Show(this, "目标是一个已存在的文件，请换一个目录。", "安装",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }

            if (LauncherPaths.LooksInstalled(target))
            {
                DialogResult overwrite = MessageBox.Show(this,
                    "该目录已存在一个 WorkBuddy Manager 安装。\n继续将覆盖程序文件（个人数据不受影响），是否继续？",
                    "安装", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (overwrite != DialogResult.Yes) { return false; }
            }

            if (!HasEnoughSpace(target))
            {
                DialogResult proceed = MessageBox.Show(this,
                    "目标磁盘可用空间不足 300MB，安装可能失败。仍要继续？",
                    "安装", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                if (proceed != DialogResult.Yes) { return false; }
            }

            _installTarget = target;
            return true;
        }

        private void StartInstall()
        {
            _pageIndex = 2;
            ShowPage(_pageProgress);
            UpdateNav();

            string target = _installTarget;
            Task.Factory.StartNew(delegate { DoInstall(target); })
                .ContinueWith(delegate(Task task)
                {
                    if (task.IsFaulted)
                    {
                        MessageBox.Show(this,
                            "安装失败：" + task.Exception.GetBaseException().Message,
                            "安装", MessageBoxButtons.OK, MessageBoxIcon.Error);
                        _lblStatus.Text = "安装失败：可点「上一步」返回重新选择目录。";
                        _pageIndex = 1;
                        ShowPage(_pageLocation);
                        UpdateNav();
                        return;
                    }
                    _lblDoneDetail.Text = "程序已安装到：\n" + target;
                    _pageIndex = 3;
                    ShowPage(_pageDone);
                    UpdateNav();
                }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void DoInstall(string target)
        {
            Diag("开始安装 target=" + target);
            Report("正在解压项目文件（约需十几秒）…");
            PayloadInstaller.ExtractPayload(target, OnExtractProgress);
            Diag("解压完成");

            Report("正在写入主程序与卸载程序…");
            PayloadInstaller.CopySelf(target);
            Diag("主程序复制完成");

            if (_chkDesktop.Checked || _chkStartMenu.Checked)
            {
                Report("正在创建快捷方式…");
                string appExe = Path.Combine(target, LauncherPaths.AppExeName);
                if (_chkDesktop.Checked)
                {
                    PayloadInstaller.CreateShortcut(LauncherPaths.DesktopShortcutPath, appExe, target, LauncherPaths.AppName);
                }
                if (_chkStartMenu.Checked)
                {
                    string menuDir = LauncherPaths.StartMenuFolder;
                    Directory.CreateDirectory(menuDir);
                    PayloadInstaller.CreateShortcut(Path.Combine(menuDir, LauncherPaths.AppName + ".lnk"),
                        appExe, target, LauncherPaths.AppName);
                    PayloadInstaller.CreateShortcut(Path.Combine(menuDir, "卸载 " + LauncherPaths.AppName + ".lnk"),
                        Path.Combine(target, LauncherPaths.UninstallExeName), target, "卸载 " + LauncherPaths.AppName);
                }
                Diag("快捷方式创建完成");
            }

            Report("正在注册卸载信息…");
            RegisterUninstallEntry(target);
            Diag("注册表写入完成");
            Report("安装完成。");
        }

        /// 安装过程诊断日志（%TEMP%\wb-install-diag.log），用于排障。
        private static readonly string DiagLogPath = Path.Combine(Path.GetTempPath(), "wb-install-diag.log");

        private static void Diag(string message)
        {
            try
            {
                File.AppendAllText(DiagLogPath,
                    DateTime.Now.ToString("HH:mm:ss.fff") + " " + message + Environment.NewLine);
            }
            catch { }
        }

        private void OnExtractProgress(int done, int total, string name)
        {
            BeginInvokeSafe(delegate
            {
                _progress.Maximum = total;
                _progress.Value = Math.Min(done, total);
                string shown = name.Length > 56 ? name.Substring(0, 56) + "…" : name;
                _lblStatus.Text = "正在解压（" + done + "/" + total + "）：" + shown;
            });
        }

        private void FinishAndMaybeLaunch()
        {
            if (_chkLaunch.Checked)
            {
                try
                {
                    System.Diagnostics.Process.Start(Path.Combine(_installTarget, LauncherPaths.AppExeName));
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this, "启动失败：" + ex.Message, LauncherPaths.AppName,
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }
            DialogResult = DialogResult.OK;
        }

        private void RegisterUninstallEntry(string target)
        {
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(LauncherPaths.UninstallRegKey))
            {
                if (key == null) { return; }
                key.SetValue("DisplayName", LauncherPaths.AppName);
                string version = ReadVersionFile(target);
                if (version.Length > 0) { key.SetValue("DisplayVersion", version); }
                key.SetValue("Publisher", LauncherPaths.AppName);
                key.SetValue("InstallLocation", target);
                key.SetValue("DisplayIcon", Path.Combine(target, LauncherPaths.AppExeName));
                key.SetValue("UninstallString", "\"" + Path.Combine(target, LauncherPaths.UninstallExeName) + "\"");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        }

        private static string ReadVersionFile(string installDir)
        {
            try
            {
                string file = Path.Combine(installDir, ".version");
                if (File.Exists(file)) { return File.ReadAllText(file, Encoding.UTF8).Trim(); }
            }
            catch { }
            return string.Empty;
        }

        private void UpdateSpaceInfo()
        {
            string text = "所需空间：约 400 MB";
            try
            {
                string rootPath = Path.GetPathRoot(Path.GetFullPath(_txtDir.Text.Trim()));
                if (!string.IsNullOrEmpty(rootPath))
                {
                    var drive = new DriveInfo(rootPath);
                    if (drive.IsReady)
                    {
                        long freeGB = drive.AvailableFreeSpace / (1024L * 1024 * 1024);
                        text += "      该磁盘可用空间：" + freeGB + " GB";
                    }
                }
            }
            catch { }
            _lblSpace.Text = text;
        }

        private static bool HasEnoughSpace(string target)
        {
            try
            {
                string rootPath = Path.GetPathRoot(Path.GetFullPath(target));
                if (string.IsNullOrEmpty(rootPath)) { return true; }
                var drive = new DriveInfo(rootPath);
                return !drive.IsReady || drive.AvailableFreeSpace >= 300L * 1024 * 1024;
            }
            catch
            {
                return true;
            }
        }

        private void Report(string status)
        {
            BeginInvokeSafe(delegate { _lblStatus.Text = status; });
        }

        private void BeginInvokeSafe(Action action)
        {
            if (!IsHandleCreated || IsDisposed) { return; }
            try { BeginInvoke(action); }
            catch { }
        }
    }
}
