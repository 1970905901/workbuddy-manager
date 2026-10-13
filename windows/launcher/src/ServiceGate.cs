using System;
using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Text;

namespace WorkBuddyLauncher
{
    /// 异步运行 PowerShell 脚本（prepare.ps1 / service-tools.ps1），输出行与退出码通过回调上报。
    internal sealed class ScriptRunner
    {
        private Process _process;

        public bool IsRunning
        {
            get
            {
                try { return _process != null && !_process.HasExited; }
                catch { return false; }
            }
        }

        public void Run(string scriptPath, string[] scriptArgs, Action<string> onLine, Action<int> onExit)
        {
            if (IsRunning) { return; }

            var process = new Process();
            process.StartInfo = BuildStartInfo(scriptPath, scriptArgs, true);
            process.EnableRaisingEvents = true;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data != null && onLine != null) { onLine(e.Data); }
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data != null && onLine != null) { onLine(e.Data); }
            };
            process.Exited += delegate
            {
                int code = -1;
                try { code = process.ExitCode; }
                catch { }
                if (onExit != null) { onExit(code); }
            };

            _process = process;
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }

        /// 同步等待脚本结束（卸载等短流程使用），返回退出码。
        public static int RunWait(string scriptPath, string[] scriptArgs)
        {
            using (Process process = Process.Start(BuildStartInfo(scriptPath, scriptArgs, false)))
            {
                if (process == null) { return -1; }
                process.WaitForExit();
                return process.ExitCode;
            }
        }

        private static ProcessStartInfo BuildStartInfo(string scriptPath, string[] scriptArgs, bool redirect)
        {
            var args = new StringBuilder();
            args.Append("-NoProfile -ExecutionPolicy Bypass -File \"").Append(scriptPath).Append("\"");
            if (scriptArgs != null)
            {
                foreach (string arg in scriptArgs) { args.Append(" \"").Append(arg).Append("\""); }
            }

            var psi = new ProcessStartInfo();
            psi.FileName = "powershell.exe";
            psi.Arguments = args.ToString();
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            if (redirect)
            {
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                // Windows PowerShell 5.1 按控制台输出编码写管道（中文 Windows 为 GBK），
                // 用 OEM 代码页解码才能正确显示脚本里的中文输出。
                Encoding oem = Encoding.GetEncoding(CultureInfo.CurrentCulture.TextInfo.OEMCodePage);
                psi.StandardOutputEncoding = oem;
                psi.StandardErrorEncoding = oem;
            }
            return psi;
        }
    }

    /// 通过 /api/healthz 判断面板服务是否已就绪。
    internal static class HealthProbe
    {
        public static bool Check(string connectHost, int port, int timeoutMs)
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(
                    "http://" + connectHost + ":" + port + "/api/healthz");
                request.Timeout = timeoutMs;
                request.Method = "GET";
                using (var response = (HttpWebResponse)request.GetResponse())
                {
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }
    }
}
