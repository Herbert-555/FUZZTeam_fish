from http import HTTPStatus

from werkzeug.serving import WSGIRequestHandler

_NGINX_REASONS = {405: 'Not Allowed', 504: 'Gateway Time-out'}


class NginxRequestHandler(WSGIRequestHandler):
    """Present the built-in dev server as nginx in the status line and headers."""

    server_version = 'nginx'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def send_response(self, code, message=None):
        try:
            message = _NGINX_REASONS.get(code, HTTPStatus(code).phrase)
        except ValueError:
            pass
        super().send_response(code, message)
