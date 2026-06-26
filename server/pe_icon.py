"""Replace icon resources in Windows PE executables using pefile.

Works on any platform (Windows/Linux/macOS) since pefile is pure Python.
"""

import os
import struct
import pefile


def inject_icon(exe_path, ico_path):
    """Replace icon resources in a Windows PE executable (modifies in-place).

    Args:
        exe_path: Path to the .exe file to modify.
        ico_path: Path to the .ico file containing replacement icons.

    Returns:
        True on success.

    Raises:
        FileNotFoundError: If exe_path or ico_path does not exist.
        ValueError: If the .ico file is malformed.
        RuntimeError: If PE modification fails.
    """
    if not os.path.exists(exe_path):
        raise FileNotFoundError(f"EXE not found: {exe_path}")
    if not os.path.exists(ico_path):
        raise FileNotFoundError(f"ICO not found: {ico_path}")

    images, dir_entries = _parse_ico(ico_path)
    if not images:
        raise ValueError("No icon images found in ICO file")

    try:
        pe = pefile.PE(exe_path)
    except Exception as e:
        raise RuntimeError(f"Failed to parse PE file: {e}")

    group_icon_data = _build_group_icon_data(dir_entries)

    _replace_resources(pe, images, group_icon_data)

    pe.write(exe_path)
    pe.close()

    # Validate the result
    try:
        pe_check = pefile.PE(exe_path)
        pe_check.close()
    except Exception as e:
        raise RuntimeError(f"Modified PE is corrupt: {e}")

    return True


def _parse_ico(ico_path):
    """Parse an .ico file and return (images, dir_entries).

    images: list of raw image byte strings
    dir_entries: list of dicts with width, height, colors, planes, bpp, size
    """
    with open(ico_path, 'rb') as f:
        data = f.read()

    if len(data) < 6:
        raise ValueError("ICO file too small")

    reserved, img_type, count = struct.unpack_from('<HHH', data, 0)
    if reserved != 0:
        raise ValueError("Invalid ICO: reserved field must be 0")
    if img_type != 1:
        raise ValueError(f"Invalid ICO: type must be 1 (ICO), got {img_type}")
    if count == 0:
        raise ValueError("ICO file contains no images")

    dir_entries = []
    images = []
    offset = 6

    for _ in range(count):
        if offset + 16 > len(data):
            raise ValueError("ICO directory entry truncated")
        entry = struct.unpack_from('<BBBBHHII', data, offset)
        width = entry[0] if entry[0] != 0 else 256
        height = entry[1] if entry[1] != 0 else 256
        colors = entry[2]
        reserved_b = entry[3]
        planes = entry[4] if entry[4] != 0 else 1
        bpp = entry[5]
        size = entry[6]
        img_offset = entry[7]

        if img_offset + size > len(data):
            raise ValueError(f"ICO image data truncated at offset {img_offset}")

        dir_entries.append({
            'width': width,
            'height': height,
            'colors': colors,
            'planes': planes,
            'bpp': bpp,
            'size': size,
        })
        images.append(data[img_offset:img_offset + size])
        offset += 16

    return images, dir_entries


def _build_group_icon_data(dir_entries):
    """Build RT_GROUP_ICON resource data from ICO directory entries."""
    buf = struct.pack('<HHH', 0, 1, len(dir_entries))
    for i, entry in enumerate(dir_entries):
        buf += struct.pack('<BBBBHHIH',
            entry['width'] if entry['width'] != 256 else 0,
            entry['height'] if entry['height'] != 256 else 0,
            entry['colors'],
            0,
            entry['planes'],
            entry['bpp'],
            entry['size'],
            i + 1,
        )
    return buf


def _replace_resources(pe, images, group_icon_data):
    """Remove old icon resources and inject new ones into the PE."""
    rsrc_dir = getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', None)
    if not rsrc_dir:
        raise RuntimeError("PE has no resource directory")

    # Collect resource entries to remove
    to_remove = []
    for entry in rsrc_dir.entries:
        eid = entry.struct.Id if hasattr(entry.struct, 'Id') else entry.struct.Name
        if eid in (3, 14):  # RT_ICON or RT_GROUP_ICON
            to_remove.append(entry)

    # Remove old icon entries from the list
    pe.DIRECTORY_ENTRY_RESOURCE.entries = [
        e for e in pe.DIRECTORY_ENTRY_RESOURCE.entries if e not in to_remove
    ]

    if images:
        _add_resource_type(pe, 3, images)     # RT_ICON
    if group_icon_data:
        _add_resource_type(pe, 14, [group_icon_data])  # RT_GROUP_ICON


def _add_resource_type(pe, res_type, data_list):
    """Add a new resource type with child entries for each data item."""
    from pefile import Structure, SectionStructure

    rsrc_dir = pe.DIRECTORY_ENTRY_RESOURCE

    # Find or create the type entry for this resource type
    type_entry = _make_directory_entry(pe, res_type, is_name=False)
    type_entry.directory = _make_resource_directory(pe)

    for idx, data in enumerate(data_list):
        name_id = idx + 1 if res_type == 3 else 1
        name_entry = _make_directory_entry(pe, name_id, is_name=False)
        lang_entry = _make_resource_data_entry(pe, data, lang=0x0804)
        name_entry.directory = _make_resource_directory(pe)
        name_entry.directory.entries = [lang_entry]
        type_entry.directory.entries.append(name_entry)

    # Append to existing resource directory entries
    entries = list(rsrc_dir.entries)
    entries.append(type_entry)
    pe.DIRECTORY_ENTRY_RESOURCE.entries = entries


def _make_directory_entry(pe, entry_id, is_name=False):
    """Create a ResourceDirEntryData for a directory level."""
    entry = pefile.ResourceDirEntryData(pe)
    entry.struct = pefile.Structure(pe.__IMAGE_RESOURCE_DIRECTORY_ENTRY_format__)
    if is_name:
        entry.struct.Name = entry_id
        entry.struct.OffsetToData |= 0x80000000
    else:
        entry.struct.Id = entry_id
        entry.struct.OffsetToData |= 0x80000000
    entry.data = None
    entry.directory = None
    return entry


def _make_resource_data_entry(pe, data, lang=0x0804):
    """Create a leaf resource data entry containing raw resource bytes."""
    entry = pefile.ResourceDirEntryData(pe)
    entry.struct = pefile.Structure(pe.__IMAGE_RESOURCE_DIRECTORY_ENTRY_format__)
    entry.struct.Id = lang
    entry.struct.OffsetToData = 0
    entry.data = pefile.ResourceDirData(pe)
    entry.data.struct = pefile.Structure(pe.__IMAGE_RESOURCE_DATA_ENTRY_format__)
    entry.data.struct.OffsetToData = 0
    entry.data.struct.Size = len(data)
    entry.data.struct.CodePage = 0
    entry.data.struct.Reserved = 0
    entry.data.lang = f'{lang:04x}'
    entry.data.data = data
    entry.directory = None
    return entry


def _make_resource_directory(pe):
    """Create an empty resource directory node."""
    rdd = pefile.ResourceDirData(pe)
    rdd.struct = pefile.Structure(pe.__IMAGE_RESOURCE_DIRECTORY_format__)
    rdd.struct.Characteristics = 0
    rdd.struct.TimeDateStamp = 0
    rdd.struct.MajorVersion = 0
    rdd.struct.MinorVersion = 0
    rdd.struct.NumberOfNamedEntries = 0
    rdd.struct.NumberOfIdEntries = 0
    rdd.entries = []
    return rdd
