using System.Diagnostics;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Vlux.Pos.ServiceHost;

public sealed class OdooWorker : BackgroundService
{
    private readonly ILogger<OdooWorker> _logger;
    private Process? _process;

    public OdooWorker(ILogger<OdooWorker> logger)
    {
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
        var root = Environment.GetEnvironmentVariable("VLUX_POS_ROOT") ?? @"C:\Program Files\VLUX\POS";
        var configDir = Environment.GetEnvironmentVariable("VLUX_POS_CONFIG") ?? Path.Combine(programData, "VLUX", "POS", "config");
        var logsDir = Environment.GetEnvironmentVariable("VLUX_POS_LOGS") ?? Path.Combine(programData, "VLUX", "POS", "logs");
        Directory.CreateDirectory(logsDir);

        var python = Path.Combine(root, "runtime", "python", "python.exe");
        var odooBin = Path.Combine(root, "runtime", "odoo", "odoo-bin");
        var odooConf = Path.Combine(configDir, "odoo.conf");
        var stdoutPath = Path.Combine(logsDir, "vluxpos.stdout.log");
        var stderrPath = Path.Combine(logsDir, "vluxpos.stderr.log");

        if (!File.Exists(python) || !File.Exists(odooBin) || !File.Exists(odooConf))
        {
            _logger.LogError("VLUX POS runtime is incomplete. python={PythonExists}, odoo={OdooExists}, config={ConfigExists}",
                File.Exists(python), File.Exists(odooBin), File.Exists(odooConf));
            while (!stoppingToken.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken);
            }
            return;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = root,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        startInfo.ArgumentList.Add(odooBin);
        startInfo.ArgumentList.Add("-c");
        startInfo.ArgumentList.Add(odooConf);

        _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        _process.OutputDataReceived += (_, e) => AppendLine(stdoutPath, e.Data);
        _process.ErrorDataReceived += (_, e) => AppendLine(stderrPath, e.Data);
        _process.Start();
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();
        _logger.LogInformation("VLUX POS Odoo process started with PID {Pid}", _process.Id);

        while (!stoppingToken.IsCancellationRequested && !_process.HasExited)
        {
            await Task.Delay(TimeSpan.FromSeconds(2), stoppingToken);
        }

        if (!stoppingToken.IsCancellationRequested && _process.HasExited)
        {
            _logger.LogError("VLUX POS Odoo process exited with code {ExitCode}", _process.ExitCode);
        }
    }

    public override async Task StopAsync(CancellationToken cancellationToken)
    {
        if (_process is { HasExited: false })
        {
            _logger.LogInformation("Stopping VLUX POS Odoo process PID {Pid}", _process.Id);
            _process.CloseMainWindow();
            await Task.Delay(TimeSpan.FromSeconds(5), cancellationToken);
            if (!_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
            }
        }
        await base.StopAsync(cancellationToken);
    }

    private static void AppendLine(string path, string? line)
    {
        if (line is null)
        {
            return;
        }
        File.AppendAllText(path, line + Environment.NewLine);
    }
}

public static class Program
{
    public static async Task Main(string[] args)
    {
        await Host.CreateDefaultBuilder(args)
            .UseWindowsService(options => options.ServiceName = "VLUXPOS")
            .ConfigureServices(services => services.AddHostedService<OdooWorker>())
            .Build()
            .RunAsync();
    }
}
