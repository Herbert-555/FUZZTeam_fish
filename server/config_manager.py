import os
import json

from .extensions import get_extension

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'data', 'exe_config.json')
ICONS_DIR = os.path.join(BASE_DIR, 'output', 'icons')
BIN_EXT = '.exe'  # Output is always Windows PE for phishing targets

# Built-in decoy document: bundled into LNK packages and served to standalone
# EXEs at /decoy when the file is not already next to the payload.
DECOY_FILE_NAME = '李泽宇简历.pdf'
DECOY_ASSET_PATH = os.path.join(BASE_DIR, 'assets', DECOY_FILE_NAME)

DEFAULT_CONFIG = {
    'icon_path': '',
    'name_template': '关于xx公司计划采购员工意外保险的方案_{token8}',
    'self_destruct': True,
    'popup_enabled': True,
    'popup_message': 'xx公司员工安全意识测试，如有疑问请联系省公司安全部门/信息科',
    'decoy_file': '',
    'behavior': 'popup',
    'server_host': '127.0.0.1',
    'server_port': 8080,
}

_extension = get_extension()
if _extension:
    DEFAULT_CONFIG.update(_extension.config_defaults())


def load_config():
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    # Merge missing keys from defaults (handles config upgrades)
    updated = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            updated = True
    if updated:
        save_config(config)
    return config


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_icon_path():
    config = load_config()
    return config.get('icon_path', '') or None


def get_name_template():
    config = load_config()
    return config.get('name_template', DEFAULT_CONFIG['name_template'])


def save_icon(file_storage):
    os.makedirs(ICONS_DIR, exist_ok=True)
    filename = 'custom_icon.ico'
    filepath = os.path.join(ICONS_DIR, filename)
    file_storage.save(filepath)

    config = load_config()
    config['icon_path'] = filepath
    save_config(config)
    return filepath


def sanitize_exe_name(name):
    name = (name or '').strip()
    if name.lower().endswith(BIN_EXT):
        name = name[:-len(BIN_EXT)]
    safe_name = "".join(c if c.isalnum() or c in '._- ' else '_' for c in name)
    safe_name = safe_name.strip(' ._')
    return safe_name


def build_filename(target_name, token):
    template = get_name_template()
    safe_name = sanitize_exe_name(target_name) or 'target'
    return template.replace('{name}', safe_name).replace('{token}', token).replace('{token8}', token[:8])


def resolve_exe_name(target_name, token, custom_filename=''):
    custom = sanitize_exe_name(custom_filename)
    if custom:
        return custom
    return build_filename(target_name, token)

