using System.Collections;
using System.Reflection;
using System.Text.Json;

if (args.Length != 1)
{
    Console.Error.WriteLine("Usage: NuxeBridge REQUEST_JSON");
    return 2;
}

try
{
    using var document = JsonDocument.Parse(File.ReadAllText(args[0]));
    var root = document.RootElement;
    var game = Path.GetFullPath(root.GetProperty("game").GetString()!);
    var output = Path.GetFullPath(root.GetProperty("output").GetString()!);
    var resources = Path.GetFullPath(root.GetProperty("res").GetString()!);
    if (Overlaps(game, output))
    {
        throw new ArgumentException("Game and output must be disjoint");
    }

    var binders = root.GetProperty("binders").EnumerateArray()
        .Select(item => item.GetString()!)
        .ToHashSet(StringComparer.OrdinalIgnoreCase);
    if (binders.Count == 0)
    {
        throw new ArgumentException("No target binders");
    }

    var assembly = Assembly.Load("Nuxe");
    var configType = assembly.GetType("Nuxe.GameConfig", throwOnError: true)!;
    var configs = ((Array)configType.GetMethod("LoadGameConfigs")!
        .Invoke(null, [resources])!).Cast<object>();
    var config = configs.Single(item =>
        (string)configType.GetProperty("BinderKeysName")!.GetValue(item)! ==
        "ArmoredCore6_PC");
    var configuredBinders =
        (IList)configType.GetProperty("Binders")!.GetValue(config)!;
    for (var index = configuredBinders.Count - 1; index >= 0; index--)
    {
        var item = configuredBinders[index]!;
        var headerPath = (string)item.GetType().GetProperty("HeaderPath")!
            .GetValue(item)!;
        if (!binders.Contains(headerPath))
        {
            configuredBinders.RemoveAt(index);
        }
    }
    if (configuredBinders.Count != binders.Count)
    {
        throw new ArgumentException("Target binder not in AC6 configuration");
    }

    Directory.CreateDirectory(output);
    var operationType = assembly.GetType("Nuxe.UnpackOperation", throwOnError: true)!;
    var operation = Activator.CreateInstance(
        operationType,
        resources,
        game,
        config,
        output,
        root.GetProperty("filter").GetString(),
        false,
        false)!;
    var progressType = assembly.GetType("Nuxe.OperationProgress", throwOnError: true)!;
    var progress = Activator.CreateInstance(
        typeof(LogProgress<>).MakeGenericType(progressType))!;

    Console.WriteLine($"Read-only game: {game}");
    Console.WriteLine($"External output: {output}");
    Console.WriteLine($"Binders: {string.Join(',', binders)}");
    operationType.GetMethod("Run", [typeof(IProgress<>).MakeGenericType(progressType),
        typeof(CancellationToken)])!.Invoke(operation, [progress, CancellationToken.None]);
    Console.WriteLine("Nuxe targeted extraction completed");
    return 0;
}
catch (TargetInvocationException exception)
{
    Console.Error.WriteLine(exception.InnerException ?? exception);
    return 1;
}
catch (Exception exception)
{
    Console.Error.WriteLine(exception);
    return 1;
}

static bool Overlaps(string first, string second)
{
    static bool IsBelow(string path, string root)
    {
        return path.Equals(root, StringComparison.OrdinalIgnoreCase) ||
            path.StartsWith(
                root.TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase);
    }

    return IsBelow(first, second) || IsBelow(second, first);
}

public sealed class LogProgress<T> : IProgress<T>
{
    public void Report(T value) => Console.WriteLine(value);
}
