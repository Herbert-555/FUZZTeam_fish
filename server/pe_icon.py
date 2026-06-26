"""Replace icon resources in Windows PE executables using direct binary patching."""

import os
import struct

RT_ICON = 3
RT_GROUP_ICON = 14
RESOURCE_DIRECTORY = 2
LANG_ZH_CN = 0x0804


def inject_icon(exe_path, ico_path):
    if not os.path.exists(exe_path):
        raise FileNotFoundError(f"EXE not found: {exe_path}")
    if not os.path.exists(ico_path):
        raise FileNotFoundError(f"ICO not found: {ico_path}")

    images, dir_entries = _parse_ico(ico_path)
    if not images:
        raise ValueError("No icon images found in ICO file")

    with open(exe_path, 'rb') as f:
        pe = bytearray(f.read())

    rsrc_rva, rsrc_size = _get_data_directory(pe, RESOURCE_DIRECTORY)
    if not rsrc_rva or not rsrc_size:
        raise RuntimeError("PE has no resource directory")

    sec_index, sec_rva, sec_raw, sec_raw_size = _find_section(pe, rsrc_rva)
    if sec_index is None:
        raise RuntimeError("Cannot find resource section")

    resources = [
        r for r in _collect_resources(pe, sec_rva, sec_raw, rsrc_rva)
        if r[0] not in (RT_ICON, RT_GROUP_ICON)
    ]
    resources.extend((RT_ICON, i + 1, LANG_ZH_CN, image) for i, image in enumerate(images))
    resources.append((RT_GROUP_ICON, 1, LANG_ZH_CN, _build_group_icon(dir_entries)))

    new_rsrc = _build_resource_section(resources, rsrc_rva)
    raw_size = _align(len(new_rsrc), _file_alignment(pe))
    padded_rsrc = new_rsrc + b'\x00' * (raw_size - len(new_rsrc))

    old_sec_end = sec_raw + sec_raw_size
    pe[sec_raw:old_sec_end] = padded_rsrc
    size_delta = raw_size - sec_raw_size
    if size_delta:
        _shift_file_offsets(pe, old_sec_end, size_delta)

    sec_hdr = _section_header_offset(pe, sec_index)
    _set_u32(pe, sec_hdr + 8, len(new_rsrc))
    _set_u32(pe, sec_hdr + 16, raw_size)
    _set_u32(pe, _data_dir_offset(pe, RESOURCE_DIRECTORY) + 4, len(new_rsrc))
    _update_size_of_image(pe)

    with open(exe_path, 'wb') as f:
        f.write(pe)

    return True


def _parse_ico(ico_path):
    with open(ico_path, 'rb') as f:
        data = f.read()

    if len(data) < 6:
        raise ValueError("ICO file too small")

    reserved, image_type, count = struct.unpack_from('<HHH', data, 0)
    if reserved != 0 or image_type != 1:
        raise ValueError("Invalid ICO header")
    if count == 0:
        raise ValueError("ICO file contains no images")

    expected_dir_end = 6 + count * 16
    if len(data) < expected_dir_end:
        raise ValueError("ICO directory is truncated")

    images = []
    entries = []
    for i in range(count):
        off = 6 + i * 16
        width, height, colors, _reserved, planes, bpp, size, image_off = struct.unpack_from('<BBBBHHII', data, off)
        if image_off + size > len(data):
            raise ValueError("ICO image data is truncated")
        entries.append({
            'width': width if width else 256,
            'height': height if height else 256,
            'colors': colors,
            'planes': planes if planes else 1,
            'bpp': bpp,
            'size': size,
        })
        images.append(data[image_off:image_off + size])

    return images, entries


def _build_group_icon(entries):
    data = bytearray(struct.pack('<HHH', 0, 1, len(entries)))
    for i, entry in enumerate(entries):
        data.extend(struct.pack(
            '<BBBBHHIH',
            0 if entry['width'] == 256 else entry['width'],
            0 if entry['height'] == 256 else entry['height'],
            entry['colors'],
            0,
            entry['planes'],
            entry['bpp'],
            entry['size'],
            i + 1,
        ))
    return bytes(data)


def _collect_resources(pe, sec_rva, sec_raw, rsrc_rva):
    root_file_off = sec_raw + (rsrc_rva - sec_rva)
    resources = []

    def walk(dir_rel, level, cur_type=None, cur_name=None):
        dir_off = root_file_off + dir_rel
        _ensure_range(pe, dir_off, 16)
        named_count = _get_u16(pe, dir_off + 12)
        id_count = _get_u16(pe, dir_off + 14)

        for i in range(named_count + id_count):
            entry_off = dir_off + 16 + i * 8
            _ensure_range(pe, entry_off, 8)
            name_raw = _get_u32(pe, entry_off)
            target_raw = _get_u32(pe, entry_off + 4)
            if name_raw & 0x80000000:
                continue

            value = name_raw
            next_type = value if level == 0 else cur_type
            next_name = value if level == 1 else cur_name

            if target_raw & 0x80000000:
                walk(target_raw & 0x7FFFFFFF, level + 1, next_type, next_name)
                continue

            data_entry_off = root_file_off + target_raw
            _ensure_range(pe, data_entry_off, 16)
            data_rva = _get_u32(pe, data_entry_off)
            data_size = _get_u32(pe, data_entry_off + 4)
            data_off = _rva_to_file_offset(pe, data_rva)
            _ensure_range(pe, data_off, data_size)
            if next_type is not None and next_name is not None:
                resources.append((next_type, next_name, value, bytes(pe[data_off:data_off + data_size])))

    walk(0, 0)
    return resources


def _build_resource_section(resources, rsrc_rva):
    tree = {}
    for res_type, name, lang, data in resources:
        tree.setdefault(res_type, {}).setdefault(name, []).append((lang, data))

    type_ids = sorted(tree)
    type_dir_offsets = {}
    name_dir_offsets = {}
    leaves = []

    offset = 16 + len(type_ids) * 8
    for res_type in type_ids:
        type_dir_offsets[res_type] = offset
        names = sorted(tree[res_type])
        offset += 16 + len(names) * 8
        for name in names:
            langs = sorted(tree[res_type][name], key=lambda item: item[0])
            name_dir_offsets[(res_type, name)] = offset
            offset += 16 + len(langs) * 8
            for lang, data in langs:
                leaves.append((res_type, name, lang, data))

    data_entry_start = offset
    data_raw_start = _align(data_entry_start + len(leaves) * 16, 4)
    data_entry_offsets = {}
    data_offsets = {}
    raw_offset = data_raw_start
    for i, leaf in enumerate(leaves):
        key = leaf[:3] + (i,)
        data_entry_offsets[key] = data_entry_start + i * 16
        data_offsets[key] = raw_offset
        raw_offset = _align(raw_offset + len(leaf[3]), 4)

    out = bytearray()
    _write_directory_header(out, len(type_ids))
    for res_type in type_ids:
        out.extend(struct.pack('<II', res_type, type_dir_offsets[res_type] | 0x80000000))

    leaf_index = 0
    for res_type in type_ids:
        names = sorted(tree[res_type])
        _write_directory_header(out, len(names))
        for name in names:
            out.extend(struct.pack('<II', name, name_dir_offsets[(res_type, name)] | 0x80000000))
        for name in names:
            langs = sorted(tree[res_type][name], key=lambda item: item[0])
            _write_directory_header(out, len(langs))
            for lang, data in langs:
                key = (res_type, name, lang, leaf_index)
                out.extend(struct.pack('<II', lang, data_entry_offsets[key]))
                leaf_index += 1

    if len(out) != data_entry_start:
        raise RuntimeError("Resource directory layout mismatch")

    for i, leaf in enumerate(leaves):
        res_type, name, lang, data = leaf
        key = (res_type, name, lang, i)
        out.extend(struct.pack('<IIII', rsrc_rva + data_offsets[key], len(data), 0, 0))

    if len(out) < data_raw_start:
        out.extend(b'\x00' * (data_raw_start - len(out)))

    for i, leaf in enumerate(leaves):
        res_type, name, lang, data = leaf
        key = (res_type, name, lang, i)
        if len(out) < data_offsets[key]:
            out.extend(b'\x00' * (data_offsets[key] - len(out)))
        out.extend(data)
        aligned = _align(len(out), 4)
        if len(out) < aligned:
            out.extend(b'\x00' * (aligned - len(out)))

    return bytes(out)


def _write_directory_header(out, id_count):
    out.extend(struct.pack('<IIHHHH', 0, 0, 0, 0, 0, id_count))


def _ensure_range(data, off, size):
    if off < 0 or size < 0 or off + size > len(data):
        raise RuntimeError("PE resource table is malformed")


def _align(value, alignment):
    return ((value + alignment - 1) // alignment) * alignment


def _get_u16(data, off):
    return struct.unpack_from('<H', data, off)[0]


def _get_u32(data, off):
    return struct.unpack_from('<I', data, off)[0]


def _set_u32(data, off, val):
    data[off:off + 4] = struct.pack('<I', val)


def _pe_offsets(pe):
    pe_sig_off = _get_u32(pe, 0x3C)
    coff_off = pe_sig_off + 4
    opt_off = coff_off + 20
    opt_size = _get_u16(pe, coff_off + 16)
    sec_off = opt_off + opt_size
    sec_count = _get_u16(pe, coff_off + 2)
    return pe_sig_off, coff_off, opt_off, sec_off, sec_count


def _optional_magic(pe):
    return _get_u16(pe, _pe_offsets(pe)[2])


def _file_alignment(pe):
    return _get_u32(pe, _pe_offsets(pe)[2] + 36)


def _section_alignment(pe):
    return _get_u32(pe, _pe_offsets(pe)[2] + 32)


def _data_dir_offset(pe, idx):
    opt_off = _pe_offsets(pe)[2]
    return opt_off + (96 if _optional_magic(pe) == 0x10B else 112) + idx * 8


def _get_data_directory(pe, idx):
    off = _data_dir_offset(pe, idx)
    return _get_u32(pe, off), _get_u32(pe, off + 4)


def _section_header_offset(pe, sec_index):
    return _pe_offsets(pe)[3] + sec_index * 40


def _find_section(pe, rva):
    _, _, _, sec_off, sec_count = _pe_offsets(pe)
    for i in range(sec_count):
        hdr = sec_off + i * 40
        virtual_size = _get_u32(pe, hdr + 8)
        virtual_address = _get_u32(pe, hdr + 12)
        raw_size = _get_u32(pe, hdr + 16)
        raw_pointer = _get_u32(pe, hdr + 20)
        size = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + size:
            return i, virtual_address, raw_pointer, raw_size
    return None, 0, 0, 0


def _rva_to_file_offset(pe, rva):
    sec_index, sec_rva, sec_raw, _ = _find_section(pe, rva)
    if sec_index is None:
        raise RuntimeError("Cannot map RVA to file offset")
    return sec_raw + (rva - sec_rva)


def _shift_file_offsets(pe, after, delta):
    _, _, _, sec_off, sec_count = _pe_offsets(pe)
    for i in range(sec_count):
        hdr = sec_off + i * 40
        raw_pointer = _get_u32(pe, hdr + 20)
        if raw_pointer >= after:
            _set_u32(pe, hdr + 20, raw_pointer + delta)


def _update_size_of_image(pe):
    _, _, opt_off, sec_off, sec_count = _pe_offsets(pe)
    section_alignment = _section_alignment(pe)
    max_end = 0
    for i in range(sec_count):
        hdr = sec_off + i * 40
        virtual_size = _get_u32(pe, hdr + 8)
        virtual_address = _get_u32(pe, hdr + 12)
        max_end = max(max_end, virtual_address + _align(virtual_size, section_alignment))
    if max_end:
        _set_u32(pe, opt_off + 56, max_end)
