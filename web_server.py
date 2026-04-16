from __future__ import annotations

import email.utils
import mimetypes
import os
import socket
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

#HTTP protocol line ending
CRLF = "\r\n"
#Standard date format used in HTTP headers
HTTP_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S GMT"
#HTTP status codes and messages supported by this server
SUPPORTED_STATUSES = {
    200: "OK",
    304: "Not Modified",
    400: "Bad Request",
    403: "Forbidden",
    404: "File Not Found",
}

#Convert a Unix timestamp to HTTP-standard GMT date string.
def format_http_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(HTTP_DATE_FORMAT)

#Parse an HTTP-date string into a timezone-aware UTC datetime object
def parse_http_date(value: str) -> Optional[datetime]:
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None

#Data structure to store parsed HTTP request information
class HttpRequest:
    def __init__(self, method: str, path: str, version: str, headers: Dict[str, str]):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers

#Multi-threaded HTTP web server that serves static files
class WebServer:
    def __init__(self, host: str, port: int, root_dir: Path, log_file: Path):
        self.host = host
        self.port = port
        self.root_dir = root_dir.resolve()
        self.log_file = log_file

        self.server_socket: Optional[socket.socket] = None
        self.shutdown_event = threading.Event()

        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    #Start the server socket and listen for incoming connections
    def start(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen(20)
            self.server_socket = sock

            print(f"Server started at http://{self.host}:{self.port}")
            print(f"Document root: {self.root_dir}")
            print(f"Log file: {self.log_file}")

            while not self.shutdown_event.is_set():
                try:
                    conn, addr = sock.accept()
                except OSError:
                    break
                worker = threading.Thread(
                    target=self.handle_client,
                    args=(conn, addr),
                    daemon=True,
                )
                worker.start()

    #Stop the server by setting the shutdown event and closing the server socket
    def stop(self) -> None:
        self.shutdown_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass

    def handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        conn.settimeout(30)
        with conn:
            keep_alive = True
            while keep_alive:
                request = self.read_request(conn)
                if request is None:
                    break

                status_code, headers, body, requested_name = self.process_request(request)
                keep_alive = self.should_keep_alive(request)

                headers["Connection"] = "keep-alive" if keep_alive else "close"
                headers["Date"] = format_http_date(datetime.now(tz=timezone.utc).timestamp())
                headers["Server"] = "Comp2322SocketServer/1.0"

                if request.method == "HEAD":
                    response_bytes = self.build_response(status_code, headers, b"")
                else:
                    response_bytes = self.build_response(status_code, headers, body)

                try:
                    conn.sendall(response_bytes)
                except OSError:
                    break

                self.log_access(
                    client_ip=addr[0],
                    request_path=requested_name,
                    status_code=status_code,
                )

    #Read and parse the HTTP request from the client connection
    def read_request(self, conn: socket.socket) -> Optional[HttpRequest]:
        data = b""
        while b"\r\n\r\n" not in data:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                return None
            except OSError:
                return None
            if not chunk:
                return None
            data += chunk
            if len(data) > 64 * 1024:
                return HttpRequest("INVALID", "/", "HTTP/1.1", {})

        raw_head = data.split(b"\r\n\r\n", 1)[0]
        try:
            text = raw_head.decode("iso-8859-1")
        except UnicodeDecodeError:
            return HttpRequest("INVALID", "/", "HTTP/1.1", {})

        lines = text.split(CRLF)
        if not lines:
            return HttpRequest("INVALID", "/", "HTTP/1.1", {})

        req_line = lines[0].strip()
        parts = req_line.split()
        if len(parts) != 3:
            return HttpRequest("INVALID", "/", "HTTP/1.1", {})

        method, path, version = parts
        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            if ":" not in line:
                return HttpRequest("INVALID", "/", "HTTP/1.1", {})
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        return HttpRequest(method=method.upper(), path=path, version=version, headers=headers)

    #Process the HTTP request and return the status code, headers, body, and requested name
    def process_request(self, request: HttpRequest) -> Tuple[int, Dict[str, str], bytes, str]:
        if request.method == "INVALID":
            return self.make_error_response(400, "/")

        if request.method not in {"GET", "HEAD"}:
            return self.make_error_response(400, request.path)

        if request.version not in {"HTTP/1.0", "HTTP/1.1"}:
            return self.make_error_response(400, request.path)

        resolved_path, requested_name, path_error = self.resolve_path(request.path)
        if path_error is not None:
            return self.make_error_response(path_error, requested_name)

        try:
            st = resolved_path.stat()
        except FileNotFoundError:
            return self.make_error_response(404, requested_name)
        except PermissionError:
            return self.make_error_response(403, requested_name)
        except OSError:
            return self.make_error_response(404, requested_name)

        if not resolved_path.is_file():
            return self.make_error_response(403, requested_name)
        if not os.access(resolved_path, os.R_OK):
            return self.make_error_response(403, requested_name)

        last_modified = format_http_date(st.st_mtime)
        if_modified_since = request.headers.get("if-modified-since")
        if if_modified_since:
            ims_dt = parse_http_date(if_modified_since)
            if ims_dt is not None:
                file_dt = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                if file_dt.replace(microsecond=0) <= ims_dt.replace(microsecond=0):
                    headers = {
                        "Content-Length": "0",
                        "Last-Modified": last_modified,
                    }
                    return 304, headers, b"", requested_name

        try:
            with resolved_path.open("rb") as f:
                body = f.read()
        except PermissionError:
            return self.make_error_response(403, requested_name)
        except OSError:
            return self.make_error_response(404, requested_name)

        content_type = mimetypes.guess_type(str(resolved_path))[0] or "application/octet-stream"
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Last-Modified": last_modified,
        }
        return 200, headers, body, requested_name

    #Resolve the path of the requested file
    def resolve_path(self, raw_path: str) -> Tuple[Path, str, Optional[int]]:
        parsed = urllib.parse.urlparse(raw_path)
        url_path = urllib.parse.unquote(parsed.path)
        if not url_path.startswith("/"):
            return self.root_dir, raw_path, 400

        if url_path == "/":
            url_path = "/index.html"

        safe_part = url_path.lstrip("/")
        candidate = (self.root_dir / safe_part).resolve()

        try:
            candidate.relative_to(self.root_dir)
        except ValueError:
            return self.root_dir, url_path, 403

        return candidate, url_path, None

    #Determine if the client should keep the connection alive based on the HTTP version and connection header
    def should_keep_alive(self, request: HttpRequest) -> bool:
        connection = request.headers.get("connection", "").lower()
        if request.version == "HTTP/1.1":
            return connection != "close"
        return connection == "keep-alive"

    #Create an error response with the given status code and requested name
    def make_error_response(self, status_code: int, requested_name: str) -> Tuple[int, Dict[str, str], bytes, str]:
        reason = SUPPORTED_STATUSES[status_code]
        body = (
            f"<html><body><h1>{status_code} {reason}</h1>"
            f"<p>Request: {requested_name}</p></body></html>"
        ).encode("utf-8")
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        return status_code, headers, body, requested_name

    #Build the HTTP response with the given status code, headers, and body
    def build_response(self, status_code: int, headers: Dict[str, str], body: bytes) -> bytes:
        reason = SUPPORTED_STATUSES[status_code]
        status_line = f"HTTP/1.1 {status_code} {reason}{CRLF}"
        header_lines = "".join(f"{k}: {v}{CRLF}" for k, v in headers.items())
        return (status_line + header_lines + CRLF).encode("iso-8859-1") + body

    #Log the access to the server with the given client IP, request path, and status code
    def log_access(self, client_ip: str, request_path: str, status_code: int) -> None:
        try:
            client_host = socket.gethostbyaddr(client_ip)[0]
            client_id = f"{client_host}/{client_ip}"
        except (socket.herror, socket.gaierror, OSError):
            client_id = client_ip
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        response_type = f"{status_code} {SUPPORTED_STATUSES[status_code]}"
        line = f"{client_id}\t{timestamp}\t{request_path}\t{response_type}\n"

        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(line)


#Ensure the sample files are present in the document root
def ensure_sample_files(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    index = root / "index.html"
    if not index.exists():
        index.write_text(
            "<html><body><h1>Comp2322 Web Server</h1><p>It works.</p></body></html>",
            encoding="utf-8",
        )
    text_file = root / "hello.txt"
    if not text_file.exists():
        text_file.write_text("Hello from the Comp2322 Python web server.\n", encoding="utf-8")


#start the server
def main() -> None:
    host = "127.0.0.1"
    port = 8080
    root_dir = Path("www")
    log_path = Path("logs/access.log")
    
    ensure_sample_files(root_dir)

    server = WebServer(host, port, root_dir, log_path)
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.stop()


if __name__ == "__main__":
    main()