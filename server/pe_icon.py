"""Replace icon resources in Windows PE executables via direct binary patching.

Works on any platform; no Windows API or Wine needed.
"""

import os
import struct

# PE constants
RT_ICON = 3
RT_GROUP_ICON = 14
IMAGE_SIZEOF_SHORT_NAME = 8


def inject_icon(exe_path, ico_path):
    """Replace icon resources in a Windows PE executable (modifies in-place).

    Returns True on success.  Raises ValueError / RuntimeError on failure.
    """
    if not os.path.exists(exe_path):
        raise FileNotFoundError(f"EXE not found: {exe_path}")
    if not os.path.exists(ico_path):
        raise FileNotFoundError(f"ICO not found: {ico_path}")

    images, dir_entries = _parse_ico(ico_path)
    if not images:
        raise ValueError("No icon images found in ICO file")

    # Read PE binary
    with open(exe_path, 'rb') as f:
        pe = bytearray(f.read())

    # Locate resource directory and section
    rsrc_rva, rsrc_size = _get_data_directory(pe, idx=2)
    if rsrc_rva == 0 or rsrc_size == 0:
        raise RuntimeError("PE has no resource directory")

    sec_index, sec_rva, sec_raw, sec_size = _find_section(pe, rsrc_rva)
    if sec_index is None:
        raise RuntimeError("Cannot find .rsrc section")

    rsrc_base = sec_raw

    # Build new resource tree (excluding old icons, adding new ones)
    new_rsrc = _rebuild_resource_tree(pe, rsrc_base, rsrc_rva, images, dir_entries)

    # Pad to FileAlignment
    file_align = _file_alignment(pe)
    if len(new_rsrc) % file_align:
        new_rsrc += b'\x00' * (file_align - len(new_rsrc) % file_align)

    # Replace section data
    old_sec_end = sec_raw + sec_size
    new_size = len(new_rsrc)
    pe[sec_raw:sec_raw + sec_size] = new_rsrc

    # Adjust file size
    size_delta = new_size - sec_size
    if size_delta != 0:
        _shift_file_offsets(pe, old_sec_end, size_delta)

    # Update section header
    _set_u32(pe, _section_header_offset(pe, sec_index) + 16, new_size)  # SizeOfRawData
    # Update resource data directory size
    _set_u32(pe, _data_dir_offset(pe, 2) + 4, len(new_rsrc))

    # Write back
    with open(exe_path, 'wb') as f:
        f.write(pe)

    return True


# ---- ICO parsing -----------------------------------------------------------

def _parse_ico(ico_path):
    with open(ico_path, 'rb') as f:
        data = f.read()

    if len(data) < 6:
        raise ValueError("ICO file too small")

    reserved, img_type, count = struct.unpack_from('<HHH', data, 0)
    if reserved != 0 or img_type != 1:
        raise ValueError("Invalid ICO header")
    if count == 0:
        raise ValueError("ICO file contains no images")

    dir_entries = []
    images = []
    off = 6
    for _ in range(count):
        w, h, colors, _res, planes, bpp, size, img_off = \
            struct.unpack_from('<BBBBHHII', data, off)
        dir_entries.append({
            'width':  w if w != 0 else 256,
            'height': h if h != 0 else 256,
            'colors': colors, 'planes': planes if planes != 0 else 1,
            'bpp': bpp, 'size': size,
        })
        images.append(data[img_off:img_off + size])
        off += 16

    return images, dir_entries


def _build_group_icon(dir_entries):
    """Build RT_GROUP_ICON resource data."""
    buf = struct.pack('<HHH', 0, 1, len(dir_entries))
    for i, e in enumerate(dir_entries):
        w = e['width'] if e['width'] != 256 else 0
        h = e['height'] if e['height'] != 256 else 0
        buf += struct.pack('<BBBBHHIH', w, h, e['colors'], 0,
                           e['planes'], e['bpp'], e['size'], i + 1)
    return buf


# ---- PE header helpers -----------------------------------------------------

def _get_u32(data, off):
    return struct.unpack_from('<I', data, off)[0]


def _set_u32(data, off, val):
    data[off:off + 4] = struct.pack('<I', val)


def _file_alignment(pe):
    """Read FileAlignment from optional header."""
    pe_sig_off = _get_u32(pe, 0x3C)
    # COFF header is 24 bytes after PE signature
    coff_off = pe_sig_off + 4
    magic = struct.unpack_from('<H', pe, coff_off)[0]
    if magic == 0x10b:  # PE32
        return _get_u32(pe, coff_off + 24 + 36)
    else:  # PE32+
        return _get_u32(pe, coff_off + 24 + 44)


def _data_dir_offset(pe, idx):
    pe_sig_off = _get_u32(pe, 0x3C)
    coff_off = pe_sig_off + 4
    magic = struct.unpack_from('<H', pe, coff_off)[0]
    opt_hdr_off = coff_off + 20
    # Data directories are at:
    #   PE32:  opt_hdr_off + 96
    #   PE32+: opt_hdr_off + 112
    if magic == 0x10b:
        return opt_hdr_off + 96 + idx * 8
    else:
        return opt_hdr_off + 112 + idx * 8


def _get_data_directory(pe, idx):
    off = _data_dir_offset(pe, idx)
    return _get_u32(pe, off), _get_u32(pe, off + 4)


def _section_header_offset(pe, sec_index):
    pe_sig_off = _get_u32(pe, 0x3C)
    coff_off = pe_sig_off + 4
    num_sections = struct.unpack_from('<H', pe, coff_off + 2)[0]
    magic = struct.unpack_from('<H', pe, coff_off)[0]
    opt_hdr_size = struct.unpack_from('<H', pe, coff_off + 16)[0]
    first_sec = coff_off + 20 + opt_hdr_size
    return first_sec + sec_index * 40


def _find_section(pe, rva):
    """Return (index, virtual_address, pointer_to_raw, size_of_raw) for the
    section containing *rva*, or (None, ...)."""
    pe_sig_off = _get_u32(pe, 0x3C)
    coff_off = pe_sig_off + 4
    num_sections = struct.unpack_from('<H', pe, coff_off + 2)[0]
    magic = struct.unpack_from('<H', pe, coff_off)[0]
    opt_hdr_size = struct.unpack_from('<H', pe, coff_off + 16)[0]
    sec_off = coff_off + 20 + opt_hdr_size
    for i in range(num_sections):
        hdr = sec_off + i * 40
        va = _get_u32(pe, hdr + 12)
        vs = _get_u32(pe, hdr + 8)
        raw = _get_u32(pe, hdr + 20)
        raw_size = _get_u32(pe, hdr + 16)
        if va <= rva < va + vs:
            return i, va, raw, raw_size
    return None, 0, 0, 0


def _shift_file_offsets(pe, after, delta):
    """Shift all file offsets >= *after* by *delta* bytes."""
    if delta == 0:
        return
    pe_sig_off = _get_u32(pe, 0x3C)
    coff_off = pe_sig_off + 4
    num_sections = struct.unpack_from('<H', pe, coff_off + 2)[0]
    magic = struct.unpack_from('<H', pe, coff_off)[0]
    opt_hdr_size = struct.unpack_from('<H', pe, coff_off + 16)[0]

    # Update section header PointerToRawData
    sec_off = coff_off + 20 + opt_hdr_size
    for i in range(num_sections):
        hdr = sec_off + i * 40
        ptr = _get_u32(pe, hdr + 20)
        if ptr >= after:
            _set_u32(pe, hdr + 20, ptr + delta)


# ---- Resource tree walk / rebuild ------------------------------------------

def _walk_collect(pe, base, rva, exclude_types):
    """Walk resource tree; return list of (type_id, name_id, lang, data_rva,
    data_size, raw_offset) for entries whose type_id is NOT in *exclude_types*."""
    result = []
    _walk_dir(pe, base, rva, 0, exclude_types, result)
    return result


def _walk_dir(pe, base, rva, level, exclude_types, result, cur_type=None):
    """Recursively walk resource directory at *rva*."""
    off = base + (rva - _section_of_rva(pe, rva)[0] if False else 0)
    # Actually: resource directory addresses are RVAs relative to .rsrc base
    # We use the base-of-section offset
    # Calculate file offset from RVA
    _, sec_rva, sec_raw, _ = _find_section(pe, rva)
    file_off = sec_raw + (rva - sec_rva)

    num_named = struct.unpack_from('<H', pe, file_off + 12)[0]
    num_id = struct.unpack_from('<H', pe, file_off + 14)[0]
    total = num_named + num_id

    for i in range(total):
        ent_off = file_off + 16 + i * 8
        name_or_id = _get_u32(pe, ent_off)
        offset = _get_u32(pe, ent_off + 4)

        if level == 0:
            cur_type = name_or_id

        if offset & 0x80000000:
            # Sub-directory
            sub_rva = offset & 0x7FFFFFFF
            _walk_dir(pe, base, sub_rva, level + 1, exclude_types, result, cur_type)
        else:
            # Data entry
            data_rva = offset
            d_off = sec_raw + (data_rva - sec_rva)
            data_file_rva = _get_u32(pe, d_off)
            data_size = _get_u32(pe, d_off + 4)
            raw_data_off = sec_raw + (data_file_rva - sec_rva)

            if cur_type not in exclude_types:
                result.append({
                    'type': cur_type,
                    'name': name_or_id,
                    'lang': name_or_id if level == 2 else 0,
                    'data': pe[raw_data_off:raw_data_off + data_size],
                    'size': data_size,
                })


def _section_of_rva(pe, rva):
    idx, va, raw, size = _find_section(pe, rva)
    return idx, va, raw, size


def _rebuild_resource_tree(pe, base, rsrc_rva, images, dir_entries):
    """Rebuild the entire .rsrc section with new icon data."""
    group_icon_data = _build_group_icon(dir_entries)

    # Collect existing resource entries, excluding old icons
    existing = _walk_collect(pe, base, rsrc_rva, {RT_ICON, RT_GROUP_ICON})

    # Build new resource section from scratch
    buf = bytearray()

    # We'll build a simple resource tree:
    #   Root dir → Type dirs → Name dirs → Lang entries → data

    # Collect by type
    by_type = {}  # type_id → [(name_id, data_bytes), ...]
    for e in existing:
        t = e['type']
        if t not in by_type:
            by_type[t] = []
        by_type[t].append((e['name'], e['data']))

    # Add new icons
    # RT_ICON (type 3)
    icon_entries = [(i + 1, img) for i, img in enumerate(images)]
    if 3 not in by_type:
        by_type[3] = []
    by_type[3] = icon_entries  # replace, don't append

    # RT_GROUP_ICON (type 14)
    if 14 not in by_type:
        by_type[14] = []
    by_type[14] = [(1, group_icon_data)]  # replace

    # Build the tree
    type_ids = sorted(by_type.keys())
    root_dir = _build_dir(buf, type_ids, by_type, is_type_level=True)

    # Write root directory at the start
    result = bytearray()
    _write_resource_tree(result, root_dir, buf)

    return bytes(result)


# Internal node types for resource tree building
class _Dir:
    def __init__(self, entries=None):
        self.entries = entries or []

class _Entry:
    def __init__(self, name_or_id, child=None, data=None):
        self.name_or_id = name_or_id
        self.child = child   # _Dir
        self.data = data     # raw bytes

class _Raw:
    def __init__(self, data):
        self.data = data


def _build_dir(buf, ids, by_type, is_type_level=False):
    entries = []
    for tid in ids:
        items = by_type[tid]
        name_entries = []
        for name_id, data in items:
            data_node = _Raw(data)
            name_entries.append(_Entry(name_id, child=None, data=data_node))
        child_dir = _Dir(name_entries)
        entries.append(_Entry(tid, child=child_dir, data=None))
    return _Dir(entries)


def _write_resource_tree(buf, root_dir, data_pool):
    """Write resource directory, entries, and data. Returns (directory_bytes,
    data_pool_offset)."""

    # Two-pass: first collect all data, then write directories with correct offsets
    all_data = []

    def collect_data(dir_node):
        for entry in dir_node.entries:
            if entry.data is not None:
                all_data.append(entry.data.data)
            if entry.child is not None:
                collect_data(entry.child)

    collect_data(root_dir)

    # Calculate data offsets (relative to start of resource section)
    # We need to know the directory size first, so we do a dry run

    # For simplicity, calculate directory size:
    dir_size = _calc_dir_size(root_dir)
    data_start = dir_size

    # Now write with correct offsets
    result = bytearray()
    _write_dir(result, root_dir, data_start, all_data, 0)

    # Append data
    for d in all_data:
        result.extend(d)

    return bytes(result)


def _calc_dir_size(dir_node):
    size = 16  # IMAGE_RESOURCE_DIRECTORY header
    size += len(dir_node.entries) * 8  # IMAGE_RESOURCE_DIRECTORY_ENTRY
    for entry in dir_node.entries:
        if entry.child is not None:
            size += _calc_dir_size(entry.child)
    return size


def _write_dir(buf, dir_node, data_base, all_data, data_idx):
    """Write directory and return updated data_idx."""
    num_named = 0
    num_id = len(dir_node.entries)

    # Header
    buf.extend(struct.pack('<IIHHHH', 0, 0, 0, num_named, num_id))

    # Calculate children offsets relative to start of buf
    # We need to know position AFTER entries before we can write children
    entries_start = len(buf)
    buf.extend(b'\x00' * (num_id * 8))  # placeholder

    children_data = bytearray()
    child_data_idx = data_idx

    for i, entry in enumerate(dir_node.entries):
        if entry.child is not None:
            # Sub-directory: write at end of current directory + previously written children
            child_offset = len(buf) + len(children_data)
            child_offset |= 0x80000000
            struct.pack_into('<I', buf, entries_start + i * 8 + 0,
                             entry.name_or_id)
            struct.pack_into('<I', buf, entries_start + i * 8 + 4,
                             child_offset)
            # Defer child writing
            child_buf = bytearray()
            child_data_idx = _write_dir(child_buf, entry.child,
                                        data_base, all_data, child_data_idx)
            children_data.extend(child_buf)
        elif entry.data is not None:
            # Data entry: write after all directories
            data_item_offset = data_base + child_data_idx
            struct.pack_into('<I', buf, entries_start + i * 8 + 0,
                             entry.name_or_id)
            struct.pack_into('<I', buf, entries_start + i * 8 + 4,
                             data_item_offset)
            # Write IMAGE_RESOURCE_DATA_ENTRY
            data_size = len(entry.data.data)
            children_data.extend(struct.pack('<IIII', data_item_offset + 16 * (len(all_data) - len(all_data)),
                                             data_size, 0, 0))
            # The actual data RVA in the data entry should point to where the
            # data will be placed after ALL data entries
            # We fix this up later
            child_data_idx += data_size

    buf.extend(children_data)
    return child_data_idx


# Actually, the resource tree building is getting overly complex.  Let me use
# a flatter, simpler approach that just works for icons specifically.
# The rebuilt implementation above is getting hard to get right.  Let me simplify.

def _rebuild_resource_tree(pe, base, rsrc_rva, images, dir_entries):
    """Build a replacement .rsrc section with new icon resources."""
    group_icon_bytes = _build_group_icon(dir_entries)
    _, sec_rva, sec_raw, _ = _find_section(pe, rsrc_rva)
    base = sec_raw

    # Collect existing non-icon resource entries as (type, path, data) tuples
    kept = _collect_entries_simple(pe, base, rsrc_rva)
    kept = [(t, n, d) for t, n, d in kept if t not in (RT_ICON, RT_GROUP_ICON)]

    # Add new icon entries
    for i, img in enumerate(images):
        kept.append((RT_ICON, i + 1, img))
    kept.append((RT_GROUP_ICON, 1, group_icon_bytes))

    # Group by type
    types = {}
    for t, n, d in kept:
        types.setdefault(t, []).append((n, d))

    # Build resource section
    return _build_resource_section(types)


def _collect_entries_simple(pe, base, rsrc_rva):
    """Walk resource tree; return flat list of (type_id, name_id, data)."""
    result = []
    _, sec_rva, sec_raw, _ = _find_section(pe, rsrc_rva)
    _walk(pe, sec_raw, sec_rva, rsrc_rva, 0, result)
    return result


def _walk(pe, sec_raw, sec_rva, rva, level, result, cur_type=None, cur_name=None):
    fo = sec_raw + (rva - sec_rva)
    num_named = struct.unpack_from('<H', pe, fo + 12)[0]
    num_id = struct.unpack_from('<H', pe, fo + 14)[0]
    total = num_named + num_id

    for i in range(total):
        off = fo + 16 + i * 8
        nid = _get_u32(pe, off)
        ofs = _get_u32(pe, off + 4)

        if level == 0:
            cur_type = nid
        elif level == 1:
            cur_name = nid

        if ofs & 0x80000000:
            _walk(pe, sec_raw, sec_rva, ofs & 0x7FFFFFFF, level + 1, result,
                  cur_type, cur_name)
        else:
            d_fo = sec_raw + (ofs - sec_rva)
            data_rva = _get_u32(pe, d_fo)
            data_size = _get_u32(pe, d_fo + 4)
            raw_off = sec_raw + (data_rva - sec_rva)
            result.append((cur_type, cur_name, bytes(pe[raw_off:raw_off + data_size])))


def _build_resource_section(types):
    """Build a valid .rsrc section from {type_id: [(name_id, data_bytes), ...]}.

    Returns the complete section bytes.
    """
    # Layout:
    #   [Root IMAGE_RESOURCE_DIRECTORY]
    #   [Root entries]
    #   [Type IMAGE_RESOURCE_DIRECTORY for each type]
    #   [Type entries]
    #   [Name IMAGE_RESOURCE_DIRECTORY for each name]
    #   [Name entries]
    #   [IMAGE_RESOURCE_DATA_ENTRY for each leaf]
    #   [Raw data for each leaf]

    type_ids = sorted(types.keys())

    # Phase 1: calculate sizes and positions
    # We'll build bottom-up: data entries first, then name dirs, then type dirs, then root

    # Data entries (leaf level)
    data_entries = []  # list of (rva_offset_for_data_entry, data_bytes)
    for tid in type_ids:
        for name_id, data in types[tid]:
            data_entries.append(data)

    # Calculate sizes
    DATA_ENTRY_SIZE = 16  # IMAGE_RESOURCE_DATA_ENTRY

    # We need to calculate the size of all directories first to know where data starts
    # Directory sizes:
    #   Root: 16 header + len(type_ids) * 8 entries
    #   Per type: 16 header + len(names) * 8 entries
    #   Per name: 16 header + 1 * 8 entries (1 language entry → data)

    # We'll build it step by step

    root_header_size = 16
    root_entries_size = len(type_ids) * 8

    # Calculate type dir sizes
    type_dirs_size = 0
    for tid in type_ids:
        names = types[tid]
        type_dirs_size += 16 + len(names) * 8
        # Each name has a sub-directory
        for _ in names:
            type_dirs_size += 16 + 1 * 8  # name dir has 1 language entry

    data_entries_total = len(data_entries) * DATA_ENTRY_SIZE
    dirs_total = root_header_size + root_entries_size + type_dirs_size
    data_start = dirs_total + data_entries_total

    # Phase 2: build the section
    buf = bytearray()

    # Placeholder for non-directory content (data entries + raw data)
    trailing = bytearray()

    # Root directory header
    buf.extend(struct.pack('<IIHHHH', 0, 0, 0, 0, len(type_ids)))

    # Root entries (point to type dirs)
    type_dir_start = 16 + len(type_ids) * 8  # right after root
    current_type_off = type_dir_start
    type_entry_map = {}  # tid → (entry_offset_in_buf, names_list)
    for tid in type_ids:
        # Entry: name_or_id=type_id, offset=type_dir_rva (with high bit)
        buf.extend(struct.pack('<II', tid, current_type_off | 0x80000000))
        entry_off = len(buf) - 4  # offset of the OffsetToData field
        type_entry_map[tid] = (entry_off, types[tid])
        # Calculate size of this type dir + its children
        names = types[tid]
        this_type_size = 16 + len(names) * 8
        for _ in names:
            this_type_size += 16 + 1 * 8  # name dir
        current_type_off += this_type_size

    # Type directories + Name directories
    data_entry_offset = dirs_total
    data_raw_offset = data_start

    for tid in type_ids:
        names = types[tid]
        # Type directory header
        buf.extend(struct.pack('<IIHHHH', 0, 0, 0, 0, len(names)))
        # Type entries → name directories
        # Name directories come right after this type dir
        name_dir_rva = len(buf) + len(names) * 8
        for name_id, data in names:
            buf.extend(struct.pack('<II', name_id, name_dir_rva | 0x80000000))
            # This name dir is at name_dir_rva
            # We need to write it now (or defer)
            # Actually, we have to defer because we're writing sequentially
            # Let me restructure...
            name_dir_rva += 16 + 8  # name dir header + 1 entry

    # This is getting messy with the sequential building approach.
    # Let me use a cleaner two-pass method.

    # Pass 1: build all directories in memory, record sizes
    # Pass 2: write everything with correct offsets

    return _build_section_two_pass(type_ids, types)


def _build_section_two_pass(type_ids, types):
    """Two-pass resource section builder."""
    # Build all directory structures in memory trees, then serialize
    from collections import OrderedDict

    # First, serialize all data
    data_blobs = []  # sequential raw data
    data_entry_info = {}  # (type, name) → index into data_blobs

    for tid in type_ids:
        for name_id, data in types[tid]:
            data_entry_info[(tid, name_id)] = len(data_blobs)
            data_blobs.append(data)

    # Calculate layout
    DATA_ENTRY_SIZE = 16

    def name_dir_size(names_count):
        return 16 + names_count * 8  # the language-level dir for a single name

    # Build name-level directories
    name_dirs = {}  # (tid, name_id) → serialized bytes (language dir)
    for tid in type_ids:
        for name_id, data in types[tid]:
            nd = bytearray()
            nd.extend(struct.pack('<IIHHHH', 0, 0, 0, 0, 1))  # 1 lang entry
            # The language entry points to data
            nd.extend(struct.pack('<II', 0x0804, 0))  # placeholder offset
            name_dirs[(tid, name_id)] = nd

    # Calculate name dir offsets
    name_dir_offsets = {}
    # First, calculate total size of root + type dirs
    root_size = 16 + len(type_ids) * 8
    offset = root_size
    for tid in type_ids:
        type_dir_header_size = 16 + len(types[tid]) * 8
        offset += type_dir_header_size
        for name_id, _ in types[tid]:
            name_dir_offsets[(tid, name_id)] = offset
            offset += len(name_dirs[(tid, name_id)])

    dirs_end = offset
    data_entries_start = dirs_end
    data_raw_start = data_entries_start + len(data_blobs) * DATA_ENTRY_SIZE

    # Patch name directory entries with correct data entry offsets
    for tid in type_ids:
        for i, (name_id, _) in enumerate(types[tid]):
            nd = name_dirs[(tid, name_id)]
            entry_idx = data_entry_info[(tid, name_id)]
            data_off = data_entries_start + entry_idx * DATA_ENTRY_SIZE
            struct.pack_into('<I', nd, 16 + 4, data_off)

    # Build type directories
    type_dirs = {}
    for tid in type_ids:
        td = bytearray()
        td.extend(struct.pack('<IIHHHH', 0, 0, 0, 0, len(types[tid])))
        for name_id, _ in types[tid]:
            nd_off = name_dir_offsets[(tid, name_id)]
            td.extend(struct.pack('<II', name_id, nd_off | 0x80000000))
        type_dirs[tid] = td

    # Build root directory
    root = bytearray()
    root.extend(struct.pack('<IIHHHH', 0, 0, 0, 0, len(type_ids)))
    type_dir_offsets = {}
    off = 16 + len(type_ids) * 8
    for tid in type_ids:
        type_dir_offsets[tid] = off
        off += len(type_dirs[tid])
        for name_id, _ in types[tid]:
            off += len(name_dirs[(tid, name_id)])

    for tid in type_ids:
        root.extend(struct.pack('<II', tid, type_dir_offsets[tid] | 0x80000000))

    # Assemble
    result = bytearray()
    result.extend(root)
    for tid in type_ids:
        result.extend(type_dirs[tid])
        for name_id, _ in types[tid]:
            result.extend(name_dirs[(tid, name_id)])

    # Data entries
    for i, data in enumerate(data_blobs):
        data_rva = data_raw_start + sum(len(data_blobs[j]) for j in range(i))
        result.extend(struct.pack('<IIII', data_rva, len(data), 0, 0))

    # Raw data
    for data in data_blobs:
        result.extend(data)

    return bytes(result)
