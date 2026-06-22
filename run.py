import os
import sys
import argparse
import threading
import secrets

sys.path.insert(0, os.path.dirname(__file__))

from server.app import create_manage_app, create_api_app


def main():
    parser = argparse.ArgumentParser(description='钓鱼演练系统 - Phishing Simulation System')
    parser.add_argument('--host', default='0.0.0.0',
                        help='服务器监听IP (默认: 0.0.0.0)')
    parser.add_argument('--manage-port', type=int, default=5000,
                        help='管理面板端口 (默认: 5000)')
    parser.add_argument('--listen-port', type=int, default=8080,
                        help='EXE回传数据端口 (默认: 8080)')
    args = parser.parse_args()

    bind_host = args.host

    # Generate a random 10-char admin path
    admin_path = secrets.token_hex(5)  # 10 hex characters

    manage_app = create_manage_app(admin_path=admin_path)
    api_app = create_api_app()

    banner = f"""
====================================================
   钓鱼演练系统 - FUZZTeam_fish
   管理面板:     http://{bind_host}:{args.manage_port}/{admin_path}/
   数据监听端口: {args.listen_port}
   请在 EXE 配置页面设置目标可达的回传地址
   请确保防火墙已放行端口 {args.manage_port}, {args.listen_port}
====================================================
"""
    print(banner)

    # Windows: auto-rebuild base EXE on every startup (async)
    if sys.platform == 'win32':
        print("[*] 正在后台构建基础 EXE (首次需要1-3分钟)...")
        def _build_base():
            try:
                from server.exe_builder import build_base_exe
                path = build_base_exe()
                print(f"[*] 基础 EXE 构建完成: {path}")
            except Exception as e:
                print(f"[!] 基础 EXE 构建失败: {e}")
        base_thread = threading.Thread(target=_build_base, daemon=True)
        base_thread.start()

    def run_api():
        api_app.run(host=bind_host, port=args.listen_port, debug=False,
                     use_reloader=False)

    # Start API listener in a background thread
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    # Start management web UI in main thread
    manage_app.run(host=bind_host, port=args.manage_port, debug=False,
                   use_reloader=False)


if __name__ == '__main__':
    main()
