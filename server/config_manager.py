import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'data', 'exe_config.json')
ICONS_DIR = os.path.join(BASE_DIR, 'output', 'icons')

DEFAULT_CONFIG = {
    'icon_path': '',
    'name_template': '关于{name}的方案_{token8}',
    'self_destruct': False,
    'popup_enabled': True,
    'popup_message': '系统不兼容，运行失败!',
}


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


def build_filename(target_name, token):
    template = get_name_template()
    safe_name = "".join(c if c.isalnum() or c in '._- ' else '_' for c in target_name)
    return template.replace('{name}', safe_name).replace('{token}', token).replace('{token8}', token[:8])

