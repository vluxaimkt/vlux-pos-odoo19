using System.Diagnostics;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Vlux.Pos.ServiceHost;

public sealed class VluxPosWorker : BackgroundService
{
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
    private const string DatabaseName = "vlux_pos";
    private const string PostgresAdminUser = "vlux_pg_admin";
    private const string OdooDbUser = "vlux_app";
    private const int PostgresPort = 55432;
    private const int OdooHttpPort = 8069;
    private const int CaddyHttpPort = 8080;
    private const int CaddyHttpsPort = 8443;

    private readonly ILogger<VluxPosWorker> _logger;
    private Process? _odoo;
    private Process? _caddy;
    private RuntimePaths? _paths;

    public VluxPosWorker(ILogger<VluxPosWorker> logger)
    {
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _paths = RuntimePaths.Create();
        _paths.EnsureDirectories();
        ValidatePayload(_paths);

        var secrets = LoadOrCreateSecrets(_paths);
        WriteOdooConfig(_paths, secrets);
        WriteCaddyfile(_paths);

        await EnsurePostgresqlAsync(_paths, secrets, stoppingToken);
        await EnsureDatabaseAsync(_paths, secrets, stoppingToken);
        await EnsureOdooInitializedAsync(_paths, stoppingToken);
        StartOdoo(_paths);
        await WaitForHttpAsync($"http://127.0.0.1:{OdooHttpPort}/vlux/health?db={DatabaseName}", stoppingToken);
        StartCaddy(_paths);
        await WaitForHttpAsync($"http://127.0.0.1:{CaddyHttpPort}/vlux/health?db={DatabaseName}", stoppingToken);

        _logger.LogInformation("VLUX POS runtime is healthy on local HTTP port {HttpPort} and local TLS port {HttpsPort}.", CaddyHttpPort, CaddyHttpsPort);
        while (!stoppingToken.IsCancellationRequested)
        {
            if (_odoo is { HasExited: true })
            {
                _logger.LogError("Odoo exited unexpectedly with code {ExitCode}.", _odoo.ExitCode);
                break;
            }
            if (_caddy is { HasExited: true })
            {
                _logger.LogError("Caddy exited unexpectedly with code {ExitCode}.", _caddy.ExitCode);
                break;
            }
            await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
        }
    }

    public override async Task StopAsync(CancellationToken cancellationToken)
    {
        StopProcess(_caddy, "Caddy");
        StopProcess(_odoo, "Odoo");
        if (_paths is not null)
        {
            try
            {
                await RunAsync(
                    _paths.PgCtlExe,
                    new[] { "-D", _paths.PostgresDataDir, "stop", "-m", "fast", "-w", "-t", "30" },
                    _paths.Root,
                    _paths.PostgresStopLog,
                    cancellationToken,
                    sensitive: false,
                    ignoreExitCode: true
                );
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "PostgreSQL stop reported an error.");
            }
        }
        await base.StopAsync(cancellationToken);
    }

    private static void ValidatePayload(RuntimePaths paths)
    {
        var requiredFiles = new[]
        {
            paths.PythonExe,
            paths.OdooBin,
            paths.PostgresExe,
            paths.InitDbExe,
            paths.PgCtlExe,
            paths.PsqlExe,
            paths.CaddyExe,
        };
        foreach (var file in requiredFiles)
        {
            if (!File.Exists(file))
            {
                throw new FileNotFoundException($"VLUX POS runtime payload is incomplete: {file}");
            }
        }
        var commitFile = Path.Combine(paths.OdooDir, "ODOO_COMMIT.txt");
        if (!File.Exists(commitFile) || File.ReadAllText(commitFile).Trim() != "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97")
        {
            throw new InvalidOperationException("Pinned Odoo runtime commit marker is missing or invalid.");
        }
    }

    private static SecretState LoadOrCreateSecrets(RuntimePaths paths)
    {
        if (File.Exists(paths.SecretsFile))
        {
            var existing = JsonSerializer.Deserialize<SecretState>(File.ReadAllText(paths.SecretsFile));
            if (existing is not null && existing.IsComplete())
            {
                return existing;
            }
        }

        var secrets = new SecretState
        {
            PostgresAdminPassword = RandomSecret(),
            DbPassword = RandomSecret(),
            OdooMasterPassword = RandomSecret(),
            InitialOwnerPassword = RandomSecret(),
            InstallerSecret = RandomSecret(),
        };
        var json = JsonSerializer.Serialize(secrets, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(paths.SecretsFile, json + Environment.NewLine, Utf8NoBom);
        return secrets;
    }

    private static string RandomSecret()
    {
        return Convert.ToBase64String(RandomNumberGenerator.GetBytes(32));
    }

    private static string PgLiteral(string value)
    {
        return value.Replace("'", "''");
    }

    private static void WriteOdooConfig(RuntimePaths paths, SecretState secrets)
    {
        var addonsPath = string.Join(
            ",",
            new[]
            {
                Path.Combine(paths.OdooDir, "addons"),
                Path.Combine(paths.Root, "addons"),
            }
        );
        var config = $"""
        [options]
        admin_passwd = {secrets.OdooMasterPassword}
        data_dir = {paths.DataDir}
        addons_path = {addonsPath}
        logfile = {paths.OdooLog}
        db_host = 127.0.0.1
        db_port = {PostgresPort}
        db_user = {OdooDbUser}
        db_password = {secrets.DbPassword}
        db_name = {DatabaseName}
        dbfilter = ^{DatabaseName}$
        list_db = False
        proxy_mode = True
        http_interface = 127.0.0.1
        http_port = {OdooHttpPort}
        workers = 0
        without_demo = True
        """;
        File.WriteAllText(paths.OdooConf, config.Replace("\r\n", "\n"), Utf8NoBom);
    }

    private static void WriteCaddyfile(RuntimePaths paths)
    {
        var caddyfile = $$"""
        {
            admin off
        }

        :{{CaddyHttpPort}} {
            reverse_proxy 127.0.0.1:{{OdooHttpPort}}
        }

        localhost:{{CaddyHttpsPort}} {
            tls internal
            reverse_proxy 127.0.0.1:{{OdooHttpPort}}
        }
        """;
        File.WriteAllText(paths.Caddyfile, caddyfile.Replace("\r\n", "\n"), Utf8NoBom);
    }

    private static async Task EnsurePostgresqlAsync(RuntimePaths paths, SecretState secrets, CancellationToken token)
    {
        if (!Directory.Exists(paths.PostgresDataDir) || !File.Exists(Path.Combine(paths.PostgresDataDir, "PG_VERSION")))
        {
            Directory.CreateDirectory(paths.PostgresDataDir);
            var pwFile = Path.Combine(paths.ConfigDir, $"pg-init-{Guid.NewGuid():N}.tmp");
            await File.WriteAllTextAsync(pwFile, secrets.PostgresAdminPassword + Environment.NewLine, token);
            try
            {
                await RunAsync(
                    paths.InitDbExe,
                    new[]
                    {
                        "-D", paths.PostgresDataDir,
                        "-U", PostgresAdminUser,
                        "-A", "scram-sha-256",
                        "--pwfile", pwFile,
                        "--encoding", "UTF8",
                    },
                    paths.Root,
                    paths.PostgresInitLog,
                    token,
                    sensitive: true
                );
            }
            finally
            {
                TryDelete(pwFile);
            }
            await File.AppendAllTextAsync(
                Path.Combine(paths.PostgresDataDir, "postgresql.conf"),
                $"{Environment.NewLine}listen_addresses = '127.0.0.1'{Environment.NewLine}port = {PostgresPort}{Environment.NewLine}password_encryption = 'scram-sha-256'{Environment.NewLine}",
                token
            );
            await File.WriteAllTextAsync(
                Path.Combine(paths.PostgresDataDir, "pg_hba.conf"),
                $"host all all 127.0.0.1/32 scram-sha-256{Environment.NewLine}host all all ::1/128 scram-sha-256{Environment.NewLine}",
                token
            );
        }

        await RunAsync(
            paths.PgCtlExe,
            new[]
            {
                "-D", paths.PostgresDataDir,
                "-l", paths.PostgresLog,
                "-o", $"-h 127.0.0.1 -p {PostgresPort}",
                "start",
                "-w",
                "-t",
                "60",
            },
            paths.Root,
            paths.PostgresStartLog,
            token,
            sensitive: false,
            ignoreExitCode: true,
            captureOutput: false
        );
    }

    private static async Task EnsureDatabaseAsync(RuntimePaths paths, SecretState secrets, CancellationToken token)
    {
        var sql = $"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{OdooDbUser}') THEN
                CREATE ROLE {OdooDbUser} LOGIN PASSWORD '{PgLiteral(secrets.DbPassword)}';
            ELSE
                ALTER ROLE {OdooDbUser} WITH LOGIN PASSWORD '{PgLiteral(secrets.DbPassword)}';
            END IF;
        END
        $$;
        SELECT 'CREATE DATABASE {DatabaseName} OWNER {OdooDbUser}' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '{DatabaseName}')\gexec
        """;
        var env = new Dictionary<string, string> { ["PGPASSWORD"] = secrets.PostgresAdminPassword };
        await RunAsync(
            paths.PsqlExe,
            new[]
            {
                "-h", "127.0.0.1",
                "-p", PostgresPort.ToString(),
                "-U", PostgresAdminUser,
                "-d", "postgres",
                "-v", "ON_ERROR_STOP=1",
            },
            paths.Root,
            paths.PostgresBootstrapLog,
            token,
            sensitive: true,
            stdin: sql,
            environment: env
        );
    }

    private static async Task EnsureOdooInitializedAsync(RuntimePaths paths, CancellationToken token)
    {
        if (File.Exists(paths.OdooInitializedMarker))
        {
            return;
        }
        await RunAsync(
            paths.PythonExe,
            new[]
            {
                paths.OdooBin,
                "-c", paths.OdooConf,
                "-d", DatabaseName,
                "-i", "vlux_core,vlux_mobile_scanner,vlux_owner",
                "--stop-after-init",
                "--without-demo=all",
            },
            paths.Root,
            paths.OdooInitLog,
            token,
            sensitive: false,
            environment: new Dictionary<string, string> { ["PYTHONPATH"] = paths.OdooDir },
            timeout: TimeSpan.FromMinutes(10)
        );
        await File.WriteAllTextAsync(paths.OdooInitializedMarker, DateTimeOffset.UtcNow.ToString("O") + Environment.NewLine, token);
    }

    private void StartOdoo(RuntimePaths paths)
    {
        _odoo = StartManagedProcess(
            paths.PythonExe,
            new[] { paths.OdooBin, "-c", paths.OdooConf, "-d", DatabaseName },
            paths.Root,
            paths.OdooStdoutLog,
            paths.OdooStderrLog,
            new Dictionary<string, string> { ["PYTHONPATH"] = paths.OdooDir }
        );
        _logger.LogInformation("Odoo process started with PID {Pid}.", _odoo.Id);
    }

    private void StartCaddy(RuntimePaths paths)
    {
        _caddy = StartManagedProcess(
            paths.CaddyExe,
            new[] { "run", "--config", paths.Caddyfile, "--adapter", "caddyfile" },
            paths.Root,
            paths.CaddyStdoutLog,
            paths.CaddyStderrLog,
            new Dictionary<string, string>
            {
                ["APPDATA"] = Path.Combine(paths.ProgramDataRoot, "caddy"),
                ["XDG_CONFIG_HOME"] = Path.Combine(paths.ProgramDataRoot, "caddy", "config"),
                ["XDG_DATA_HOME"] = Path.Combine(paths.ProgramDataRoot, "caddy", "data"),
                ["HOME"] = Path.Combine(paths.ProgramDataRoot, "caddy"),
            }
        );
        _logger.LogInformation("Caddy process started with PID {Pid}.", _caddy.Id);
    }

    private static Process StartManagedProcess(
        string fileName,
        string[] args,
        string workingDirectory,
        string stdoutPath,
        string stderrPath,
        IDictionary<string, string>? environment = null
    )
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var arg in args)
        {
            startInfo.ArgumentList.Add(arg);
        }
        if (environment is not null)
        {
            foreach (var item in environment)
            {
                startInfo.Environment[item.Key] = item.Value;
            }
        }
        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) => AppendLine(stdoutPath, e.Data);
        process.ErrorDataReceived += (_, e) => AppendLine(stderrPath, e.Data);
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        return process;
    }

    private static async Task WaitForHttpAsync(string url, CancellationToken token)
    {
        using var client = new HttpClient(new HttpClientHandler
        {
            ServerCertificateCustomValidationCallback = HttpClientHandler.DangerousAcceptAnyServerCertificateValidator,
        })
        {
            Timeout = TimeSpan.FromSeconds(5),
        };
        var deadline = DateTimeOffset.UtcNow.AddMinutes(3);
        Exception? lastError = null;
        while (DateTimeOffset.UtcNow < deadline && !token.IsCancellationRequested)
        {
            try
            {
                using var response = await client.GetAsync(url, token);
                if (response.StatusCode == HttpStatusCode.OK)
                {
                    return;
                }
                lastError = new InvalidOperationException($"HTTP {(int)response.StatusCode} from {url}");
            }
            catch (Exception ex)
            {
                lastError = ex;
            }
            await Task.Delay(TimeSpan.FromSeconds(3), token);
        }
        throw new TimeoutException($"Timed out waiting for {url}: {lastError?.Message}");
    }

    private static async Task RunAsync(
        string fileName,
        string[] args,
        string workingDirectory,
        string logPath,
        CancellationToken token,
        bool sensitive,
        string? stdin = null,
        IDictionary<string, string>? environment = null,
        TimeSpan? timeout = null,
        bool ignoreExitCode = false,
        bool captureOutput = true
    )
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = captureOutput,
            RedirectStandardError = captureOutput,
            RedirectStandardInput = stdin is not null,
        };
        foreach (var arg in args)
        {
            startInfo.ArgumentList.Add(arg);
        }
        if (environment is not null)
        {
            foreach (var item in environment)
            {
                startInfo.Environment[item.Key] = item.Value;
            }
        }

        Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
        if (!sensitive)
        {
            await File.AppendAllTextAsync(logPath, $"> {fileName} {string.Join(" ", args)}{Environment.NewLine}", token);
        }
        else
        {
            await File.AppendAllTextAsync(logPath, $"> {Path.GetFileName(fileName)} [sensitive arguments redacted]{Environment.NewLine}", token);
        }

        using var timeoutSource = timeout.HasValue ? new CancellationTokenSource(timeout.Value) : null;
        using var linked = timeoutSource is null
            ? CancellationTokenSource.CreateLinkedTokenSource(token)
            : CancellationTokenSource.CreateLinkedTokenSource(token, timeoutSource.Token);
        using var process = new Process { StartInfo = startInfo };
        process.Start();
        if (stdin is not null)
        {
            await process.StandardInput.WriteAsync(stdin.AsMemory(), linked.Token);
            process.StandardInput.Close();
        }
        Task<string> stdoutTask = captureOutput ? process.StandardOutput.ReadToEndAsync(linked.Token) : Task.FromResult("");
        Task<string> stderrTask = captureOutput ? process.StandardError.ReadToEndAsync(linked.Token) : Task.FromResult("");
        await process.WaitForExitAsync(linked.Token);
        await File.AppendAllTextAsync(logPath, await stdoutTask + await stderrTask, token);
        if (process.ExitCode != 0 && !ignoreExitCode)
        {
            throw new InvalidOperationException($"{Path.GetFileName(fileName)} exited with code {process.ExitCode}. See {logPath}");
        }
    }

    private static void StopProcess(Process? process, string name)
    {
        if (process is not { HasExited: false })
        {
            return;
        }
        try
        {
            process.CloseMainWindow();
            if (!process.WaitForExit(TimeSpan.FromSeconds(5)))
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Best effort during service shutdown.
        }
    }

    private static void AppendLine(string path, string? line)
    {
        if (line is null)
        {
            return;
        }
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.AppendAllText(path, line + Environment.NewLine);
    }

    private static void TryDelete(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch
        {
            // Temporary secret file cleanup is best effort.
        }
    }
}

public sealed class RuntimePaths
{
    public required string Root { get; init; }
    public required string ProgramDataRoot { get; init; }
    public required string ConfigDir { get; init; }
    public required string DataDir { get; init; }
    public required string LogsDir { get; init; }
    public required string PythonExe { get; init; }
    public required string OdooDir { get; init; }
    public required string OdooBin { get; init; }
    public required string OdooConf { get; init; }
    public required string OdooInitializedMarker { get; init; }
    public required string OdooLog { get; init; }
    public required string OdooInitLog { get; init; }
    public required string OdooStdoutLog { get; init; }
    public required string OdooStderrLog { get; init; }
    public required string PostgresExe { get; init; }
    public required string InitDbExe { get; init; }
    public required string PgCtlExe { get; init; }
    public required string PsqlExe { get; init; }
    public required string PostgresDataDir { get; init; }
    public required string PostgresLog { get; init; }
    public required string PostgresInitLog { get; init; }
    public required string PostgresStartLog { get; init; }
    public required string PostgresStopLog { get; init; }
    public required string PostgresBootstrapLog { get; init; }
    public required string CaddyExe { get; init; }
    public required string Caddyfile { get; init; }
    public required string CaddyStdoutLog { get; init; }
    public required string CaddyStderrLog { get; init; }
    public required string SecretsFile { get; init; }

    public static RuntimePaths Create()
    {
        var programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
        var root = Environment.GetEnvironmentVariable("VLUX_POS_ROOT") ?? @"C:\Program Files\VLUX\POS";
        var programDataRoot = Environment.GetEnvironmentVariable("VLUX_POS_DATA_ROOT") ?? Path.Combine(programData, "VLUX", "POS");
        var configDir = Environment.GetEnvironmentVariable("VLUX_POS_CONFIG") ?? Path.Combine(programDataRoot, "config");
        var dataDir = Path.Combine(programDataRoot, "data");
        var logsDir = Environment.GetEnvironmentVariable("VLUX_POS_LOGS") ?? Path.Combine(programDataRoot, "logs");
        var postgresData = Path.Combine(programDataRoot, "postgresql", "data");
        return new RuntimePaths
        {
            Root = root,
            ProgramDataRoot = programDataRoot,
            ConfigDir = configDir,
            DataDir = dataDir,
            LogsDir = logsDir,
            PythonExe = Path.Combine(root, "runtime", "python", "python.exe"),
            OdooDir = Path.Combine(root, "runtime", "odoo"),
            OdooBin = Path.Combine(root, "runtime", "odoo", "odoo-bin"),
            OdooConf = Path.Combine(configDir, "odoo.conf"),
            OdooInitializedMarker = Path.Combine(dataDir, ".odoo_initialized"),
            OdooLog = Path.Combine(logsDir, "odoo.log"),
            OdooInitLog = Path.Combine(logsDir, "odoo-init.log"),
            OdooStdoutLog = Path.Combine(logsDir, "vluxpos.stdout.log"),
            OdooStderrLog = Path.Combine(logsDir, "vluxpos.stderr.log"),
            PostgresExe = Path.Combine(root, "runtime", "postgresql", "bin", "postgres.exe"),
            InitDbExe = Path.Combine(root, "runtime", "postgresql", "bin", "initdb.exe"),
            PgCtlExe = Path.Combine(root, "runtime", "postgresql", "bin", "pg_ctl.exe"),
            PsqlExe = Path.Combine(root, "runtime", "postgresql", "bin", "psql.exe"),
            PostgresDataDir = postgresData,
            PostgresLog = Path.Combine(logsDir, "postgresql.log"),
            PostgresInitLog = Path.Combine(logsDir, "postgresql-init.log"),
            PostgresStartLog = Path.Combine(logsDir, "postgresql-start.log"),
            PostgresStopLog = Path.Combine(logsDir, "postgresql-stop.log"),
            PostgresBootstrapLog = Path.Combine(logsDir, "postgresql-bootstrap.log"),
            CaddyExe = Path.Combine(root, "runtime", "caddy", "caddy.exe"),
            Caddyfile = Path.Combine(configDir, "Caddyfile"),
            CaddyStdoutLog = Path.Combine(logsDir, "caddy.stdout.log"),
            CaddyStderrLog = Path.Combine(logsDir, "caddy.stderr.log"),
            SecretsFile = Path.Combine(configDir, "secrets.json"),
        };
    }

    public void EnsureDirectories()
    {
        foreach (var directory in new[]
        {
            ProgramDataRoot,
            ConfigDir,
            DataDir,
            Path.Combine(ProgramDataRoot, "filestore"),
            Path.Combine(ProgramDataRoot, "backups"),
            LogsDir,
            Path.GetDirectoryName(PostgresDataDir)!,
        })
        {
            Directory.CreateDirectory(directory);
        }
    }
}

public sealed class SecretState
{
    public string PostgresAdminPassword { get; set; } = "";
    public string DbPassword { get; set; } = "";
    public string OdooMasterPassword { get; set; } = "";
    public string InitialOwnerPassword { get; set; } = "";
    public string InstallerSecret { get; set; } = "";

    public bool IsComplete()
    {
        return !string.IsNullOrWhiteSpace(PostgresAdminPassword)
            && !string.IsNullOrWhiteSpace(DbPassword)
            && !string.IsNullOrWhiteSpace(OdooMasterPassword)
            && !string.IsNullOrWhiteSpace(InitialOwnerPassword)
            && !string.IsNullOrWhiteSpace(InstallerSecret);
    }
}

public static class Program
{
    public static async Task Main(string[] args)
    {
        await Host.CreateDefaultBuilder(args)
            .UseWindowsService(options => options.ServiceName = "VLUXPOS")
            .ConfigureServices(services => services.AddHostedService<VluxPosWorker>())
            .Build()
            .RunAsync();
    }
}
