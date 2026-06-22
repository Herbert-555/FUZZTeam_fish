"""Extract document icons from Windows: registry, known DLLs, or generate fallback."""
import os
import struct
import ctypes
from ctypes import wintypes

ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'icons')

SHGFI_ICON = 0x000000100
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_NORMAL = 0x80


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


def _get_hicon_for_ext(ext):
    """Get HICON for a file extension. Returns hIcon or None."""
    shfi = SHFILEINFOW()
    ret = ctypes.windll.shell32.SHGetFileInfoW(
        f"*.{ext}", FILE_ATTRIBUTE_NORMAL,
        ctypes.byref(shfi), ctypes.sizeof(shfi),
        SHGFI_ICON | SHGFI_USEFILEATTRIBUTES)
    if ret and shfi.hIcon:
        return shfi.hIcon
    return None


def _get_hicon_from_dll(dll_path, index):
    """Extract an icon from a DLL/exe by index using ExtractIconEx."""
    hicon_array = (wintypes.HICON * 1)()
    count = ctypes.windll.shell32.ExtractIconExW(dll_path, index, None,
                                                   ctypes.byref(hicon_array), 1)
    if count > 0 and hicon_array[0]:
        return hicon_array[0]
    return None


def _search_registry_icon(ext):
    """Search Windows registry for the DefaultIcon of a file extension."""
    import winreg
    try:
        # Get ProgID from extension
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'.{ext}')
        progid = winreg.QueryValue(key, None)
        key.Close()

        # Get DefaultIcon from ProgID
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f'{progid}\\DefaultIcon')
        icon_str = winreg.QueryValue(key, None)
        key.Close()

        # Parse "path,index" or "path,-index"
        parts = icon_str.rsplit(',', 1)
        dll_path = parts[0].strip('"')
        index = int(parts[1]) if len(parts) > 1 else 0

        if os.path.exists(dll_path):
            hicon = _get_hicon_from_dll(dll_path, abs(index))
            if hicon:
                return hicon
    except Exception:
        pass
    return None


def _save_hicon_as_ico(hicon, out_path):
    """Save an HICON as .ico using PIL for reliability."""
    try:
        from PIL import Image
        import io as io_mod

        # Use DrawIconEx to a temporary bitmap, then PIL to save as ICO
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Default icon size
        size = 48
        hdc = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc)
        hbm = gdi32.CreateCompatibleBitmap(hdc, size, size)
        old_bm = gdi32.SelectObject(hdc_mem, hbm)
        user32.DrawIconEx(hdc_mem, 0, 0, hicon, size, size, 0, None, 3)

        # Read pixels into buffer
        row_size = ((size * 32 + 31) // 32) * 4
        buf_size = row_size * size
        pixels = ctypes.create_string_buffer(buf_size)

        bi_buf = ctypes.create_string_buffer(40)
        struct.pack_into('<IiiHHIIiiII', bi_buf, 0,
                         40, size, size, 1, 32, 0, buf_size, 0, 0, 0, 0)
        gdi32.GetDIBits(hdc_mem, hbm, 0, size, pixels, ctypes.byref(bi_buf), 0)

        # Cleanup GDI
        gdi32.SelectObject(hdc_mem, old_bm)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc)
        user32.DestroyIcon(hicon)

        # Build ICO with PIL
        # BGRA -> RGBA, bottom-up -> top-down
        raw = pixels.raw
        rgba = bytearray(size * size * 4)
        for y in range(size):
            src_row = (size - 1 - y) * row_size
            dst_row = y * size * 4
            for x in range(size):
                src = src_row + x * 4
                dst = dst_row + x * 4
                rgba[dst] = raw[src + 2]      # R
                rgba[dst + 1] = raw[src + 1]  # G
                rgba[dst + 2] = raw[src]      # B
                rgba[dst + 3] = raw[src + 3]  # A

        img = Image.frombytes('RGBA', (size, size), bytes(rgba))
        buf = io_mod.BytesIO()
        img.save(buf, format='ICO', sizes=[(size, size)])
        buf.seek(0)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'wb') as f:
            f.write(buf.read())
        return True
    except Exception as e:
        print(f"    save_ico failed: {e}")
        return False


def _search_dll_icons(ext):
    """Try known Windows DLL locations for document icons."""
    # Known DLL indices for document-like icons in imageres.dll
    # These are typical on Windows 10/11
    candidates = [
        (r'C:\Windows\System32\imageres.dll', [2, 3, 4, 101, 102, 103]),
        (r'C:\Windows\System32\shell32.dll', [1, 2, 3, 4, 5]),
        (r'C:\Windows\System32\moricons.dll', [1, 2, 3]),
    ]
    for dll, indices in candidates:
        if not os.path.exists(dll):
            continue
        for idx in indices:
            hicon = _get_hicon_from_dll(dll, idx)
            if hicon:
                return hicon
    return None


def _generate_pillow_icon(ext, out_path, color, label):
    """Generate a simple document icon using Pillow as last resort."""
    from PIL import Image, ImageDraw, ImageFont
    import io as io_mod

    size = 48
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 5
    fold = 14
    draw.rectangle([margin, margin, size - margin, size - margin], fill='white', outline='#cccccc', width=1)
    draw.polygon([
        (size - margin - fold, size - margin),
        (size - margin - fold, size - margin - fold),
        (size - margin, size - margin - fold),
    ], fill='#e0e0e0', outline='#cccccc')
    draw.rectangle([margin, margin, size - margin - fold, margin + 16], fill=color)
    try:
        font = ImageFont.truetype("segoeui.ttf", 9)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", 9)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((size - tw) // 2, margin + 2), label, fill='white', font=font)
    buf = io_mod.BytesIO()
    img.save(buf, format='ICO', sizes=[(48, 48)])
    buf.seek(0)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(buf.read())


def _extract_one(ext):
    """Try all methods to get an icon for an extension."""
    out_path = os.path.join(ICONS_DIR, f'{ext}.ico')

    # 1. Try the exact file the user mentioned (Downloads)
    import glob
    user = os.environ.get('USERPROFILE', '')
    search_paths = [
        os.path.join(user, 'Downloads', f'*.{ext}'),
        os.path.join(user, 'Desktop', f'*.{ext}'),
        os.path.join(user, 'Documents', f'*.{ext}'),
    ]
    for sp in search_paths:
        files = glob.glob(sp)
        if files:
            shfi = SHFILEINFOW()
            ret = ctypes.windll.shell32.SHGetFileInfoW(
                files[0], 0, ctypes.byref(shfi), ctypes.sizeof(shfi), SHGFI_ICON)
            if ret and shfi.hIcon:
                if _save_hicon_as_ico(shfi.hIcon, out_path):
                    return out_path

    # 2. Try SHGetFileInfo with USEFILEATTRIBUTES
    hicon = _get_hicon_for_ext(ext)
    if hicon:
        if _save_hicon_as_ico(hicon, out_path):
            return out_path

    # 3. Search registry
    hicon = _search_registry_icon(ext)
    if hicon:
        if _save_hicon_as_ico(hicon, out_path):
            return out_path

    # 4. Try system DLLs
    hicon = _search_dll_icons(ext)
    if hicon:
        if _save_hicon_as_ico(hicon, out_path):
            return out_path

    return None


def generate_builtin_icons():
    """Generate icons, trying multiple strategies."""
    print("[*] Extracting document icons...")
    presets = {
        'xlsx': ('#217346', 'XLSX'),
        'docx': ('#2B579A', 'DOCX'),
        'zip': ('#8B6F4E', 'ZIP'),
        'pdf': ('#D9382B', 'PDF'),
    }
    results = {}
    for ext, (color, label) in presets.items():
        out_path = os.path.join(ICONS_DIR, f'{ext}.ico')
        path = _extract_one(ext)
        if path:
            results[ext] = path
            print(f"  [+] {ext}: real system icon")
            continue
        # Fallback
        _generate_pillow_icon(ext, out_path, color, label)
        results[ext] = out_path
        print(f"  [+] {ext}: generated (no system icon found)")
    return results


if __name__ == '__main__':
    generate_builtin_icons()
