import os
import sys
import argparse
import socket
import threading

sys.path.insert(0, os.path.dirname(__file__))

from server.app import create_manage_app, create_api_app


def get_local_ip():
    """Detect the local IP address that can reach external networks."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def main():
    parser = argparse.ArgumentParser(description='钓鱼演练系统 - Phishing Simulation System')
    parser.add_argument('--host', default='0.0.0.0',
                        help='服务器监听IP (默认: 0.0.0.0)')
    parser.add_argument('--manage-port', type=int, default=5000,
                        help='管理面板端口 (默认: 5000)')
    parser.add_argument('--listen-port', type=int, default=8080,
                        help='EXE回传数据端口 (默认: 8080)')
    args = parser.parse_args()

    # Determine the callback IP that EXEs will use
    local_ip = args.host if args.host != '0.0.0.0' else get_local_ip()

    manage_app = create_manage_app(
        listen_host=local_ip,
        listen_port=args.listen_port
    )
    api_app = create_api_app()

    banner = f"""
====================================================
   钓鱼演练系统 - Phishing Simulation System
   管理面板:     http://0.0.0.0:{args.manage_port}/fishfish/
   数据监听端口: {args.listen_port}
   生成的EXE将回传数据到: http://{local_ip}:{args.listen_port}
   请确保防火墙已放行端口 {args.manage_port}, {args.listen_port}
====================================================
"""
    print(banner)

    def run_api():
        api_app.run(host=args.host, port=args.listen_port, debug=False,
                     use_reloader=False)

    # Start API listener in a background thread
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    # Start management web UI in main thread
    manage_app.run(host=args.host, port=args.manage_port, debug=True,
                   use_reloader=False)


if __name__ == '__main__':
    main()
