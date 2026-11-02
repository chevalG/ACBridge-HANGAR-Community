// ACBridge HANGAR desktop shell.
//
// The launcher owns exactly the Python service it started and the WebView2
// controller it created. Closing the HANGAR window asks that owned service to
// stop, waits for its process tree, then exits. It never enumerates or kills
// unrelated Python processes by name.
#include <windows.h>
#include <windowsx.h>
#include <shlobj.h>
#include <shellapi.h>
#include <wrl.h>
#include <WebView2.h>
#include <winhttp.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <string>
#include <vector>

#include "resource.h"

using namespace Microsoft::WRL;

namespace {

constexpr wchar_t kAppTitle[] = L"ACBridge HANGAR";
constexpr wchar_t kMutexName[] = L"Local\\ACBridge.HANGAR.Desktop";
constexpr wchar_t kEventName[] = L"Local\\ACBridge.HANGAR.Activate";
constexpr wchar_t kWindowClass[] = L"HANGAR.Desktop.Window";
constexpr int kStartupTimeoutMs = 45000;
constexpr int kShutdownTimeoutMs = 20000;

struct WindowState {
    HWND hwnd = nullptr;
    HWND webview_hwnd = nullptr;
    HICON icon_large = nullptr;
    HICON icon_small = nullptr;
    PROCESS_INFORMATION service{};
    HANDLE service_job = nullptr;
    HANDLE activate_event = nullptr;
    HANDLE single_instance = nullptr;
    bool service_started = false;
    bool reused_service = false;
    bool shutting_down = false;
    bool webview_ready = false;
    bool webview_failed = false;
    std::wstring data_dir;
    std::wstring url;
    std::wstring startup_error;
};

WindowState g_state;
ComPtr<ICoreWebView2Environment> g_environment;
ComPtr<ICoreWebView2Controller> g_controller;
ComPtr<ICoreWebView2> g_webview;

std::wstring Join(const std::wstring& left, const wchar_t* right) {
    std::wstring value = left;
    if (!value.empty() && value.back() != L'\\' && value.back() != L'/') {
        value.push_back(L'\\');
    }
    value += right;
    return value;
}

std::wstring ExecutableDirectory() {
    wchar_t path[MAX_PATH * 4]{};
    GetModuleFileNameW(nullptr, path, static_cast<DWORD>(std::size(path)));
    std::wstring value(path);
    const size_t slash = value.find_last_of(L"\\/");
    return slash == std::wstring::npos ? value : value.substr(0, slash);
}

std::wstring KnownFolder(REFKNOWNFOLDERID id) {
    wchar_t* raw = nullptr;
    if (FAILED(SHGetKnownFolderPath(id, 0, nullptr, &raw)) || !raw) {
        return {};
    }
    std::wstring value(raw);
    CoTaskMemFree(raw);
    return value;
}

std::wstring EnvironmentValue(const wchar_t* name) {
    DWORD size = GetEnvironmentVariableW(name, nullptr, 0);
    if (!size) {
        return {};
    }
    std::wstring value(size, L'\0');
    if (!GetEnvironmentVariableW(name, value.data(), size)) {
        return {};
    }
    value.resize(size - 1);
    return value;
}

std::string HttpGet(const std::wstring& host, int port, const wchar_t* path) {
    HINTERNET session = WinHttpOpen(L"HANGAR-Health/1.0",
                                    WINHTTP_ACCESS_TYPE_NO_PROXY,
                                    WINHTTP_NO_PROXY_NAME,
                                    WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) {
        return {};
    }
    WinHttpSetTimeouts(session, 2000, 2000, 2000, 2000);
    HINTERNET connection = WinHttpConnect(session, host.c_str(),
                                          static_cast<INTERNET_PORT>(port), 0);
    if (!connection) {
        WinHttpCloseHandle(session);
        return {};
    }
    HINTERNET request = WinHttpOpenRequest(connection, L"GET", path, nullptr,
                                           WINHTTP_NO_REFERER,
                                           WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    if (!request) {
        WinHttpCloseHandle(connection);
        WinHttpCloseHandle(session);
        return {};
    }
    std::string body;
    if (WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                           WINHTTP_NO_REQUEST_DATA, 0, 0, 0) &&
        WinHttpReceiveResponse(request, nullptr)) {
        DWORD available = 0;
        do {
            available = 0;
            WinHttpQueryDataAvailable(request, &available);
            if (available == 0) {
                break;
            }
            std::vector<char> chunk(std::min<DWORD>(available, 4096));
            DWORD read = 0;
            if (!WinHttpReadData(request, chunk.data(),
                                 static_cast<DWORD>(chunk.size()), &read)) {
                break;
            }
            body.append(chunk.data(), read);
            if (body.size() > 8192) {
                break;
            }
        } while (available > 0);
    }
    WinHttpCloseHandle(request);
    WinHttpCloseHandle(connection);
    WinHttpCloseHandle(session);
    return body;
}

std::wstring JsonString(const std::string& body, const char* key) {
    const std::string needle = std::string("\"") + key + "\": \"";
    const size_t start = body.find(needle);
    if (start == std::string::npos) {
        return {};
    }
    size_t at = start + needle.size();
    std::wstring value;
    while (at < body.size() && body[at] != '"') {
        if (body[at] == '\\' && at + 1 < body.size()) {
            ++at;
            if (body[at] == '\\' || body[at] == '"') {
                value.push_back(static_cast<wchar_t>(body[at]));
            }
        } else {
            value.push_back(static_cast<wchar_t>(body[at]));
        }
        ++at;
    }
    return value;
}

int JsonInt(const std::string& body, const char* key) {
    const std::string needle = std::string("\"") + key + "\": ";
    const size_t start = body.find(needle);
    if (start == std::string::npos) {
        return 0;
    }
    return std::atoi(body.c_str() + start + needle.size());
}

bool ProbeExistingService(const std::wstring& data_dir, std::wstring& url) {
    const std::wstring state_path = Join(data_dir, L"launcher-server.json");
    HANDLE file = CreateFileW(state_path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (!file) {
        return false;
    }
    DWORD size = GetFileSize(file, nullptr);
    std::string body;
    if (size > 0 && size < 4096) {
        body.resize(size);
        DWORD read = 0;
        if (ReadFile(file, body.data(), size, &read, nullptr)) {
            body.resize(read);
        }
    }
    CloseHandle(file);
    const int port = JsonInt(body, "port");
    const int pid = JsonInt(body, "pid");
    const std::wstring recorded = JsonString(body, "data_dir");
    if (port <= 0 || pid <= 0 || recorded.empty()) {
        return false;
    }
    const std::wstring recorded_resolved = [&] {
        wchar_t buffer[MAX_PATH * 4]{};
        if (GetFullPathNameW(recorded.c_str(), static_cast<DWORD>(std::size(buffer)),
                             buffer, nullptr) == 0) {
            return recorded;
        }
        return std::wstring(buffer);
    }();
    const std::wstring data_resolved = [&] {
        wchar_t buffer[MAX_PATH * 4]{};
        if (GetFullPathNameW(data_dir.c_str(), static_cast<DWORD>(std::size(buffer)),
                             buffer, nullptr) == 0) {
            return data_dir;
        }
        return std::wstring(buffer);
    }();
    if (_wcsicmp(recorded_resolved.c_str(), data_resolved.c_str()) != 0) {
        return false;
    }
    HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, static_cast<DWORD>(pid));
    if (!process) {
        return false;
    }
    const bool alive = WaitForSingleObject(process, 0) == WAIT_TIMEOUT;
    CloseHandle(process);
    if (!alive) {
        return false;
    }
    const std::string health = HttpGet(L"127.0.0.1", port, L"/api/health");
    if (health.find("\"ok\": true") == std::string::npos) {
        return false;
    }
    url = L"http://127.0.0.1:" + std::to_wstring(port) + L"/";
    return true;
}

void SetEnvironmentValue(const wchar_t* name, const std::wstring& value) {
    SetEnvironmentVariableW(name, value.c_str());
}

void EnsureDirectory(const std::wstring& path) {
    if (path.empty()) {
        return;
    }
    std::wstring current;
    for (size_t i = 0; i <= path.size(); ++i) {
        const wchar_t ch = i < path.size() ? path[i] : L'\0';
        const bool boundary = ch == L'\\' || ch == L'/' || ch == L'\0';
        if (boundary && current.size() > 2) {
            CreateDirectoryW(current.c_str(), nullptr);
        }
        if (!ch) {
            break;
        }
        current.push_back(ch);
    }
}

void ShowError(const std::wstring& message) {
    MessageBoxW(nullptr, message.c_str(), kAppTitle, MB_OK | MB_ICONERROR);
}

// The desktop shell speaks the Windows UI language: Chinese when the system
// is zh, otherwise English. The web UI has its own in-app language choice.
bool IsChineseShellUi() {
    return PRIMARYLANGID(GetUserDefaultUILanguage()) == LANG_CHINESE;
}

const wchar_t* ShellMsg(const wchar_t* zh, const wchar_t* en) {
    return IsChineseShellUi() ? zh : en;
}

void EnsureDirectory(const std::wstring& path);

void LogDiagnostic(const std::wstring& message) {
    std::wstring base = KnownFolder(FOLDERID_LocalAppData);
    if (base.empty()) {
        return;
    }
    EnsureDirectory(Join(Join(Join(base, L"ACBridge"), L"HANGAR"), L"data"));
    const std::wstring path = Join(Join(Join(Join(base, L"ACBridge"), L"HANGAR"),
                                         L"data"), L"desktop.log");
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                              OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (!file) {
        return;
    }
    std::string text = "\r\n";
    for (wchar_t ch : message) {
        text.push_back(static_cast<char>(ch));
    }
    DWORD written = 0;
    WriteFile(file, text.data(), static_cast<DWORD>(text.size()), &written, nullptr);
    CloseHandle(file);
}

bool IsOwnedServiceAlive() {
    if (!g_state.service_started || !g_state.service.hProcess) {
        return false;
    }
    return WaitForSingleObject(g_state.service.hProcess, 0) == WAIT_TIMEOUT;
}

void CloseProcessHandle(PROCESS_INFORMATION& info) {
    if (info.hThread) {
        CloseHandle(info.hThread);
        info.hThread = nullptr;
    }
    if (info.hProcess) {
        CloseHandle(info.hProcess);
        info.hProcess = nullptr;
    }
}

bool StartOwnedService(const std::wstring& root, const std::wstring& data_dir,
                       std::wstring& url, std::wstring& error) {
    const std::wstring python = Join(Join(root, L"runtime"), L"python.exe");
    const std::wstring module = L"hangar";
    if (GetFileAttributesW(python.c_str()) == INVALID_FILE_ATTRIBUTES ||
        GetFileAttributesW(Join(root, L"hangar").c_str()) == INVALID_FILE_ATTRIBUTES) {
        error = ShellMsg(L"HANGAR 安装不完整：缺少 runtime/python.exe 或 hangar 模块。\n"
                L"请完整解压 ZIP 后重试。",
                L"The HANGAR installation is incomplete: runtime/python.exe or the "
                L"hangar module is missing.\n"
                L"Please extract the ZIP fully and try again.");
        return false;
    }
    EnsureDirectory(data_dir);
    // If the user already started this installation manually (START.cmd) for
    // the same data directory, reuse it and do not claim its lifecycle. Closing
    // the HANGAR window must not stop a service the launcher did not create.
    if (ProbeExistingService(data_dir, url)) {
        g_state.reused_service = true;
        return true;
    }
    const std::wstring state_path = Join(data_dir, L"launcher-server.json");
    DeleteFileW(state_path.c_str());
    const std::wstring log_path = Join(data_dir, L"server.log");
    HANDLE log = CreateFileW(log_path.c_str(), FILE_APPEND_DATA,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                             OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (!log) {
        error = ShellMsg(L"无法创建服务日志：", L"Could not create the service log: ") + log_path;
        return false;
    }
    // The service is started directly, so the launcher owns the actual
    // process that serves HTTP rather than an intermediate launcher script.
    const std::wstring command = L"\"" + python + L"\" -s -B -m " + module +
        L" --port 0 --no-browser --data-dir \"" + data_dir + L"\"";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = log;
    startup.hStdError = log;

    PROCESS_INFORMATION info{};
    SetEnvironmentValue(L"HANGAR_DATA", data_dir);
    SetEnvironmentValue(L"PYTHONUTF8", L"1");
    SetEnvironmentValue(L"PYTHONDONTWRITEBYTECODE", L"1");
    SetEnvironmentValue(L"PYTHONNOUSERSITE", L"1");
    SetEnvironmentValue(L"HANGAR_QUIET_LAUNCH", L"1");
    SetEnvironmentValue(L"HANGAR_DESKTOP_SHELL", L"1");
    SetEnvironmentValue(L"HANGAR_SERVICE_PARENT", std::to_wstring(GetCurrentProcessId()));

    const BOOL started = CreateProcessW(
        python.c_str(), const_cast<LPWSTR>(command.c_str()), nullptr, nullptr, TRUE,
        CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr, root.c_str(), &startup, &info);
    CloseHandle(log);
    if (!started) {
        error = ShellMsg(L"无法启动 HANGAR 本地服务。\n错误代码：",
                         L"Could not start the HANGAR local service.\nError code: ") +
                std::to_wstring(GetLastError());
        return false;
    }

    // A job object gives us a second, explicit ownership boundary for all
    // descendants of this service, even if the Python process fails to
    // clean up after itself.
    g_state.service_job = CreateJobObjectW(nullptr, nullptr);
    if (g_state.service_job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(g_state.service_job,
                                JobObjectExtendedLimitInformation,
                                &limits, sizeof(limits));
        AssignProcessToJobObject(g_state.service_job, info.hProcess);
    }

    g_state.service = info;
    g_state.service_started = true;
    ResumeThread(info.hThread);

    // The service writes its own state file once the loopback port is bound.
    // Reading that file avoids buffering server output and keeps stdout free
    // for the rotating log handler.
    const ULONGLONG deadline = GetTickCount64() + kStartupTimeoutMs;
    while (GetTickCount64() < deadline) {
        if (WaitForSingleObject(info.hProcess, 0) == WAIT_OBJECT_0) {
            DWORD code = 0;
            GetExitCodeProcess(info.hProcess, &code);
            error = ShellMsg(L"HANGAR 本地服务启动后立即退出。\n",
                             L"The HANGAR local service exited right after starting.\n");
            error += ShellMsg(L"退出代码：", L"Exit code: ") + std::to_wstring(code) + L"\n";
            error += ShellMsg(L"详细日志：", L"Detailed log: ") + Join(data_dir, L"server.log");
            return false;
        }
        HANDLE state_file = CreateFileW(state_path.c_str(), GENERIC_READ,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                                        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (state_file) {
            DWORD size = GetFileSize(state_file, nullptr);
            std::string body;
            if (size > 0 && size < 4096) {
                body.resize(size);
                DWORD read = 0;
                if (ReadFile(state_file, body.data(), size, &read, nullptr)) {
                    body.resize(read);
                }
            }
            CloseHandle(state_file);
            const std::string marker = "\"url\": \"http://127.0.0.1:";
            const size_t at = body.find(marker);
            if (at != std::string::npos) {
                const size_t start = at + std::strlen("\"url\": \"");
                const size_t end = body.find('"', start);
                if (end != std::string::npos) {
                    url = std::wstring(body.begin() + start, body.begin() + end);
                    return true;
                }
            }
        }
        Sleep(50);
    }
    error = ShellMsg(L"HANGAR 本地服务未能在 45 秒内启动。\n",
                     L"The HANGAR local service did not start within 45 seconds.\n");
    error += ShellMsg(L"详细日志：", L"Detailed log: ") + Join(data_dir, L"server.log");
    return false;
}

void ResizeWebView() {
    if (!g_state.webview_hwnd || !g_controller) {
        return;
    }
    RECT bounds{};
    GetClientRect(g_state.hwnd, &bounds);
    SetWindowPos(g_state.webview_hwnd, nullptr, 0, 0,
                 bounds.right - bounds.left, bounds.bottom - bounds.top,
                 SWP_NOZORDER | SWP_NOACTIVATE);
    g_controller->put_Bounds(bounds);
}

void FocusWebView() {
    if (g_controller) {
        g_controller->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
    }
}

void RequestServiceShutdown() {
    if (!g_state.service_started || !g_state.service.hProcess) {
        return;
    }
    if (!IsOwnedServiceAlive()) {
        return;
    }
    const std::wstring stop = Join(Join(ExecutableDirectory(), L"runtime"), L"python.exe");
    const std::wstring script = Join(ExecutableDirectory(), L"stop.py");
    if (GetFileAttributesW(stop.c_str()) == INVALID_FILE_ATTRIBUTES ||
        GetFileAttributesW(script.c_str()) == INVALID_FILE_ATTRIBUTES) {
        return;
    }
    SetEnvironmentValue(L"HANGAR_DATA", g_state.data_dir);
    SetEnvironmentValue(L"PYTHONNOUSERSITE", L"1");
    SetEnvironmentValue(L"PYTHONDONTWRITEBYTECODE", L"1");
    const std::wstring command = L"\"" + stop + L"\" -s -B \"" + script + L"\"";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION info{};
    if (!CreateProcessW(stop.c_str(), const_cast<LPWSTR>(command.c_str()),
                        nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                        nullptr, ExecutableDirectory().c_str(), &startup, &info)) {
        return;
    }
    WaitForSingleObject(info.hProcess, 15000);
    CloseProcessHandle(info);
}

void DestroyWebView() {
    g_webview.Reset();
    if (g_controller) {
        g_controller->Close();
        g_controller.Reset();
    }
    g_environment.Reset();
    g_state.webview_hwnd = nullptr;
    g_state.webview_ready = false;
}

void ShutdownOwnedService() {
    if (g_state.reused_service) {
        g_state.reused_service = false;
        return;
    }
    if (!g_state.service_started || !g_state.service.hProcess) {
        return;
    }
    RequestServiceShutdown();
    if (WaitForSingleObject(g_state.service.hProcess, kShutdownTimeoutMs) == WAIT_TIMEOUT) {
        // The service refused a graceful stop. It is still exactly our child,
        // so terminate that tree rather than guessing by process name.
        if (g_state.service_job) {
            TerminateJobObject(g_state.service_job, 1);
        } else {
            TerminateProcess(g_state.service.hProcess, 1);
        }
        WaitForSingleObject(g_state.service.hProcess, 5000);
    }
    CloseProcessHandle(g_state.service);
    if (g_state.service_job) {
        CloseHandle(g_state.service_job);
        g_state.service_job = nullptr;
    }
    g_state.service_started = false;
}

void ApplyWindowIcon() {
    if (!g_state.hwnd) {
        return;
    }
    if (g_state.icon_large) {
        SendMessageW(g_state.hwnd, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(g_state.icon_large));
    }
    if (g_state.icon_small) {
        SendMessageW(g_state.hwnd, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(g_state.icon_small));
    }
}

void LoadWindowIcons() {
    const std::wstring icon_path = Join(ExecutableDirectory(), L"HANGAR.ico");
    g_state.icon_large = static_cast<HICON>(LoadImageW(
        nullptr, icon_path.c_str(), IMAGE_ICON, 256, 256, LR_LOADFROMFILE));
    g_state.icon_small = static_cast<HICON>(LoadImageW(
        nullptr, icon_path.c_str(), IMAGE_ICON, 32, 32, LR_LOADFROMFILE));
    if (!g_state.icon_large) {
        g_state.icon_large = LoadIconW(nullptr, IDI_APPLICATION);
    }
    if (!g_state.icon_small) {
        g_state.icon_small = LoadIconW(nullptr, IDI_APPLICATION);
    }
}

void BeginClose() {
    if (g_state.shutting_down) {
        return;
    }
    g_state.shutting_down = true;
    DestroyWebView();
    ShutdownOwnedService();
    if (g_state.hwnd) {
        DestroyWindow(g_state.hwnd);
        g_state.hwnd = nullptr;
    }
}

void ShowStartupFailure() {
    g_state.startup_error += ShellMsg(L"\n\n日志：", L"\n\nLog: ") + Join(g_state.data_dir, L"server.log");
    ShowError(g_state.startup_error);
    BeginClose();
}

void OnWebViewCreated(HRESULT result, ICoreWebView2Controller* controller) {
    if (g_state.shutting_down) {
        return;
    }
    if (FAILED(result) || !controller) {
        g_state.webview_failed = true;
        g_state.startup_error = ShellMsg(L"HANGAR 界面组件初始化失败。\n"
            L"请确认 Microsoft Edge WebView2 Runtime 已安装，然后重新启动。\n"
            L"下载：https://go.microsoft.com/fwlink/p/?LinkId=2124703",
            L"HANGAR failed to initialize its UI component.\n"
            L"Please verify that the Microsoft Edge WebView2 Runtime is installed, then restart.\n"
            L"Download: https://go.microsoft.com/fwlink/p/?LinkId=2124703");
        PostMessageW(g_state.hwnd, WM_APP + 3, 0, 0);
        return;
    }
    g_controller = controller;
    g_controller->get_CoreWebView2(&g_webview);
    if (!g_webview) {
        g_state.webview_failed = true;
        g_state.startup_error = ShellMsg(L"HANGAR 界面控制器不可用。",
                                         L"The HANGAR UI controller is unavailable.");
        PostMessageW(g_state.hwnd, WM_APP + 3, 0, 0);
        return;
    }

    RECT bounds{};
    GetClientRect(g_state.hwnd, &bounds);
    g_controller->put_Bounds(bounds);
    g_controller->put_IsVisible(TRUE);
    g_controller->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
    // The controller owns its child HWND; resizing the controller is enough.
    // ICoreWebView2 deliberately does not expose get_Window in the public API.
    g_state.webview_ready = true;
    g_webview->Navigate(g_state.url.c_str());
}

void InitializeWebView() {
    std::wstring profile = Join(g_state.data_dir, L"desktop-profile");
    EnsureDirectory(profile);
    HRESULT result = CreateCoreWebView2EnvironmentWithOptions(
        nullptr, profile.c_str(), nullptr,
        Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
            [](HRESULT env_result, ICoreWebView2Environment* environment) -> HRESULT {
                if (FAILED(env_result) || !environment) {
                    if (!g_state.shutting_down) {
                        g_state.webview_failed = true;
                        g_state.startup_error = ShellMsg(L"HANGAR 界面组件初始化失败。\n"
                            L"请安装 Microsoft Edge WebView2 Runtime 后重试。\n"
                            L"下载：https://go.microsoft.com/fwlink/p/?LinkId=2124703",
                            L"HANGAR failed to initialize its UI component.\n"
                            L"Please install the Microsoft Edge WebView2 Runtime and retry.\n"
                            L"Download: https://go.microsoft.com/fwlink/p/?LinkId=2124703");
                        PostMessageW(g_state.hwnd, WM_APP + 3, 0, 0);
                    }
                    return S_OK;
                }
                g_environment = environment;
                environment->CreateCoreWebView2Controller(
                    g_state.hwnd,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                        [](HRESULT controller_result, ICoreWebView2Controller* controller) -> HRESULT {
                            OnWebViewCreated(controller_result, controller);
                            return S_OK;
                        }).Get());
                return S_OK;
            }).Get());
    if (FAILED(result)) {
        g_state.webview_failed = true;
        g_state.startup_error = ShellMsg(L"HANGAR 界面组件初始化失败。\n"
            L"请安装 Microsoft Edge WebView2 Runtime 后重试。\n"
            L"下载：https://go.microsoft.com/fwlink/p/?LinkId=2124703",
            L"HANGAR failed to initialize its UI component.\n"
            L"Please install the Microsoft Edge WebView2 Runtime and retry.\n"
            L"Download: https://go.microsoft.com/fwlink/p/?LinkId=2124703");
        PostMessageW(g_state.hwnd, WM_APP + 3, 0, 0);
    }
}

void OnWindowCreated() {
    std::wstring local = KnownFolder(FOLDERID_LocalAppData);
    if (local.empty()) {
        g_state.startup_error = ShellMsg(L"无法读取 LOCALAPPDATA，HANGAR 不能创建用户数据目录。",
            L"Could not read LOCALAPPDATA; HANGAR cannot create the user data directory.");
        PostMessageW(g_state.hwnd, WM_APP + 3, 0, 0);
        return;
    }
    const std::wstring override = EnvironmentValue(L"HANGAR_DATA");
    g_state.data_dir = override.empty()
        ? Join(Join(Join(local, L"ACBridge"), L"HANGAR"), L"data")
        : override;
    EnsureDirectory(g_state.data_dir);
    const std::wstring root = ExecutableDirectory();
    if (!StartOwnedService(root, g_state.data_dir, g_state.url, g_state.startup_error)) {
        PostMessageW(g_state.hwnd, WM_APP + 3, 0, 0);
        return;
    }
    InitializeWebView();
}

void ActivateExistingInstance() {
    HANDLE event = OpenEventW(EVENT_MODIFY_STATE, FALSE, kEventName);
    if (event) {
        SetEvent(event);
        CloseHandle(event);
    }
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
    case WM_CREATE:
        g_state.hwnd = hwnd;
        ApplyWindowIcon();
        PostMessageW(hwnd, WM_APP + 1, 0, 0);
        return 0;
    case WM_APP + 1:
        OnWindowCreated();
        return 0;
    case WM_APP + 2:
        if (IsIconic(hwnd)) {
            ShowWindow(hwnd, SW_RESTORE);
        }
        SetForegroundWindow(hwnd);
        FocusWebView();
        return 0;
    case WM_APP + 3:
        if (!g_state.startup_error.empty()) {
            ShowStartupFailure();
        }
        return 0;
    case WM_SIZE:
        ResizeWebView();
        return 0;
    case WM_SETFOCUS:
        FocusWebView();
        return 0;
    case WM_GETMINMAXINFO: {
        auto* info = reinterpret_cast<MINMAXINFO*>(lparam);
        info->ptMinTrackSize.x = 980;
        info->ptMinTrackSize.y = 640;
        return 0;
    }
    case WM_CLOSE:
        BeginClose();
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }
}

bool ParseSwitch(const wchar_t* argument, const wchar_t* name) {
    return argument && _wcsicmp(argument, name) == 0;
}

} // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    bool no_window = false;
    bool health_only = false;
    bool stop_only = false;
    for (int i = 1; i < argc; ++i) {
        no_window = no_window || ParseSwitch(argv[i], L"--no-window");
        health_only = health_only || ParseSwitch(argv[i], L"--health-check");
        stop_only = stop_only || ParseSwitch(argv[i], L"--stop-service");
    }
    if (argv) {
        LocalFree(argv);
    }

    g_state.single_instance = CreateMutexW(nullptr, TRUE, kMutexName);
    if (!g_state.single_instance) {
        ShowError(ShellMsg(L"无法创建 HANGAR 单实例锁。",
                           L"Could not create the HANGAR single-instance lock."));
        return 2;
    }
    const bool already_running = WaitForSingleObject(g_state.single_instance, 0) != WAIT_OBJECT_0;
    if (already_running) {
        if (!stop_only) {
            ActivateExistingInstance();
        }
        CloseHandle(g_state.single_instance);
        return 0;
    }

    g_state.activate_event = CreateEventW(nullptr, FALSE, FALSE, kEventName);
    LoadWindowIcons();

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.hInstance = instance;
    window_class.lpfnWndProc = WindowProc;
    window_class.lpszClassName = kWindowClass;
    window_class.hIcon = g_state.icon_large;
    window_class.hIconSm = g_state.icon_small;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground = CreateSolidBrush(RGB(12, 17, 23));
    if (!RegisterClassExW(&window_class)) {
        ShowError(ShellMsg(L"无法注册 HANGAR 窗口类。",
                           L"Could not register the HANGAR window class."));
        return 2;
    }

    const std::wstring root = ExecutableDirectory();
    const std::wstring local = KnownFolder(FOLDERID_LocalAppData);
    const std::wstring override = EnvironmentValue(L"HANGAR_DATA");
    g_state.data_dir = !override.empty() ? override :
        (local.empty() ? Join(root, L"data") :
         Join(Join(Join(local, L"ACBridge"), L"HANGAR"), L"data"));
    EnsureDirectory(g_state.data_dir);

    if (health_only) {
        std::wstring url;
        std::wstring error;
        if (!StartOwnedService(root, g_state.data_dir, url, error)) {
            OutputDebugStringW(error.c_str());
            ShutdownOwnedService();
            return 1;
        }
        ShutdownOwnedService();
        return 0;
    }

    HWND hwnd = CreateWindowExW(
        0, kWindowClass, kAppTitle, WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 1600, 1000,
        nullptr, nullptr, instance, nullptr);
    if (!hwnd) {
        ShowError(ShellMsg(L"无法创建 HANGAR 主窗口。",
                           L"Could not create the HANGAR main window."));
        return 2;
    }
    if (no_window) {
        ShowWindow(hwnd, SW_HIDE);
    } else {
        ShowWindow(hwnd, SW_SHOW);
        UpdateWindow(hwnd);
    }

    SetTimer(hwnd, 1, 250, nullptr);
    bool quit = false;
    while (!quit) {
        MSG message{};
        const bool has_message = PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE);
        if (has_message) {
            if (message.message == WM_QUIT) {
                quit = true;
                break;
            }
            TranslateMessage(&message);
            DispatchMessageW(&message);
            continue;
        }
        if (WaitForSingleObject(g_state.activate_event, 100) == WAIT_OBJECT_0) {
            PostMessageW(hwnd, WM_APP + 2, 0, 0);
        }
        if (g_state.service_started && !IsOwnedServiceAlive() && !g_state.shutting_down) {
            // The service died unexpectedly. Keep the window open only long
            // enough to show the actionable failure, then clean up.
            g_state.startup_error = ShellMsg(L"HANGAR 本地服务意外退出。\n"
                L"请重新启动 HANGAR；日志位于 data\\server.log。",
                L"The HANGAR local service exited unexpectedly.\n"
                L"Please restart HANGAR; the log is at data\\server.log.");
            PostMessageW(hwnd, WM_APP + 3, 0, 0);
        }
    }
    if (IsWindow(hwnd)) {
        BeginClose();
    } else {
        ShutdownOwnedService();
    }
    if (g_state.activate_event) {
        CloseHandle(g_state.activate_event);
    }
    CloseHandle(g_state.single_instance);
    CoUninitialize();
    return 0;
}
