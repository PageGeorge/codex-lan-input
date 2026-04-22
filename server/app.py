import json
import locale
import logging
import mimetypes
import re
import socket
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import injector

HOST = "0.0.0.0"
PORT = 8765
STATIC_DIR = Path(__file__).with_name("static")


def detect_wlan_ipv4():
    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None

    output = _decode_command_output(result.stdout)
    adapters = _parse_ipconfig_adapters(output)

    preferred_adapter = _find_adapter_by_name(adapters, "WLAN")
    if preferred_adapter and preferred_adapter["ipv4_addresses"]:
        return preferred_adapter["ipv4_addresses"][0]

    wireless_adapter = _find_wireless_adapter(adapters)
    if wireless_adapter and wireless_adapter["ipv4_addresses"]:
        return wireless_adapter["ipv4_addresses"][0]

    fallback_adapter = _find_best_private_adapter(adapters)
    if fallback_adapter and fallback_adapter["ipv4_addresses"]:
        return fallback_adapter["ipv4_addresses"][0]

    hostname_ip = _find_best_private_hostname_ip()
    if hostname_ip:
        return hostname_ip

    return None


def _decode_command_output(raw_output: bytes):
    encodings = []
    for encoding in (
        "utf-8",
        locale.getpreferredencoding(False),
        "cp936",
        "gbk",
        "mbcs",
    ):
        if encoding and encoding not in encodings:
            encodings.append(encoding)

    for encoding in encodings:
        try:
            return raw_output.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw_output.decode("utf-8", errors="replace")


def _parse_ipconfig_adapters(output: str):
    adapters = []
    current = None

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        header_match = re.match(r"^(?P<label>.+ adapter )(?P<name>.+):$", stripped)
        if header_match:
            current = {
                "header": stripped,
                "name": header_match.group("name"),
                "ipv4_addresses": [],
                "default_gateways": [],
                "media_disconnected": False,
            }
            adapters.append(current)
            continue

        if current is None:
            continue

        if "Media disconnected" in stripped:
            current["media_disconnected"] = True
            continue

        if "IPv4" in stripped and ":" in stripped:
            ipv4 = stripped.rsplit(":", 1)[-1].strip()
            if _is_ipv4_address(ipv4):
                current["ipv4_addresses"].append(ipv4)
            continue

        if "Default Gateway" in stripped and ":" in stripped:
            gateway = stripped.rsplit(":", 1)[-1].strip()
            if _is_ipv4_address(gateway):
                current["default_gateways"].append(gateway)

    return adapters


def _find_adapter_by_name(adapters, target_name: str):
    target = target_name.casefold()
    for adapter in adapters:
        if adapter["name"].casefold() == target:
            return adapter
    return None


def _find_wireless_adapter(adapters):
    for adapter in adapters:
        if "Wireless LAN adapter" in adapter["header"] and adapter["ipv4_addresses"]:
            return adapter
    return None


def _find_best_private_adapter(adapters):
    blacklist = ("vmware", "tailscale", "flclash", "loopback", "teredo")

    for adapter in adapters:
        name = adapter["name"].casefold()
        if any(token in name for token in blacklist):
            continue
        if adapter["media_disconnected"]:
            continue
        if not adapter["default_gateways"]:
            continue

        for ipv4 in adapter["ipv4_addresses"]:
            if _is_private_lan_ipv4(ipv4):
                return adapter

    return None


def _is_ipv4_address(value: str):
    parts = value.split(".")
    if len(parts) != 4:
        return False

    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def _is_private_lan_ipv4(ipv4: str):
    parts = [int(part) for part in ipv4.split(".")]
    first, second = parts[0], parts[1]

    if first == 10:
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    if first == 192 and second == 168:
        return True
    return False


def _find_best_private_hostname_ip():
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return None

    candidates = []
    for ipv4 in addresses:
        if not _is_ipv4_address(ipv4):
            continue
        if ipv4.startswith("169.254."):
            continue
        if not _is_private_lan_ipv4(ipv4):
            continue
        candidates.append(ipv4)

    if not candidates:
        return None

    def sort_key(ipv4: str):
        first = int(ipv4.split(".")[0])
        if first == 10:
            return (0, ipv4)
        if first == 172:
            return (1, ipv4)
        return (2, ipv4)

    return sorted(candidates, key=sort_key)[0]


class CodexLanInputServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "CodexLanInput/0.1"

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._serve_file(STATIC_DIR / "index.html")

        if path == "/health":
            return self._send_json(HTTPStatus.OK, {"ok": True})

        if path.startswith("/static/"):
            return self._serve_static_path(path)

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            if path == "/api/send":
                payload = self._read_json_body()
                text = payload.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    return self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "message": "文本不能为空"},
                    )

                logging.info(
                    "Send request from %s with %d characters",
                    self.client_address[0],
                    len(text),
                )
                injector.paste_text(text)
                return self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "message": "文本已粘贴到 Codex"},
                )

            if path == "/api/enter":
                logging.info("Enter request from %s", self.client_address[0])
                injector.press_enter()
                return self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "message": "已发送回车"},
                )

            return self._send_json(
                HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"}
            )
        except injector.BusyError as exc:
            logging.warning("Input action rejected: %s", exc)
            return self._send_json(
                HTTPStatus.CONFLICT, {"ok": False, "message": str(exc)}
            )
        except injector.InjectionError as exc:
            logging.exception("Input action failed")
            return self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "message": str(exc)},
            )
        except ValueError as exc:
            return self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)}
            )

    def log_message(self, format, *args):
        logging.info("%s - %s", self.address_string(), format % args)

    def _serve_static_path(self, request_path: str):
        relative_path = request_path.removeprefix("/static/")
        target = (STATIC_DIR / relative_path).resolve()

        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self._send_json(
                HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"}
            )

        return self._serve_file(target)

    def _serve_file(self, file_path: Path):
        if not file_path.is_file():
            return self._send_json(
                HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"}
            )

        content = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(file_path.name)
        if not content_type:
            content_type = "application/octet-stream"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self):
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("缺少请求体")

        try:
            body_size = int(content_length)
        except ValueError as exc:
            raise ValueError("Content-Length 头无效") from exc

        raw_body = self.rfile.read(body_size)
        if not raw_body:
            raise ValueError("缺少请求体")

        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 请求体无效") from exc

    def _send_json(self, status: HTTPStatus, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    server = CodexLanInputServer((HOST, PORT), RequestHandler)
    logging.info("Serving Codex LAN input on http://%s:%s", HOST, PORT)
    logging.info("Local URL: http://127.0.0.1:%s/", PORT)

    wlan_ip = detect_wlan_ipv4()
    if wlan_ip:
        logging.info("Phone URL: http://%s:%s/", wlan_ip, PORT)
    else:
        logging.info('Phone URL: not detected from "Wireless LAN adapter WLAN"')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Server interrupted by user")
    finally:
        server.server_close()
        logging.info("Server stopped")


if __name__ == "__main__":
    main()
