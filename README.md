Comp2322 Project - Multi-threaded Web Server (Python)

1) Requirements
- Python 3.9+.

2) Run server <br>
From this project folder, run: <br>
python3 web_server.py <br>
Server will listen on `127.0.0.1:8080`, document root is `./www`, and the access log is written to `logs/access.log`.

3) Test examples

- GET text file: <br>
curl -i http://127.0.0.1:8080/hello.txt

- GET image file (PNG): <br>
curl -i http://127.0.0.1:8080/pic.png

- HEAD: <br>
curl -I http://127.0.0.1:8080/index.html

- 404 File Not Found: <br>
curl -i http://127.0.0.1:8080/not_exist.html

- 403 Forbidden (path traversal): <br>
curl --path-as-is -i "http://127.0.0.1:8080/../secret.txt"

- 400 Bad Request (unsupported method): <br>
curl -i -X POST http://127.0.0.1:8080/index.html

- Keep-alive / close: <br>
curl -i -H "Connection: keep-alive" http://127.0.0.1:8080/index.html <br>
curl -i -H "Connection: close" http://127.0.0.1:8080/index.html

- Last-Modified and If-Modified-Since: <br>
curl -i http://127.0.0.1:8080/index.html <br>
copy the Last-Modified value from the above response: <br>
curl -i -H "If-Modified-Since: Sat, 01 Jan 2100 00:00:00 GMT" http://127.0.0.1:8080/index.html

4) Log file
- Path: logs/access.log
- One request per line.
- Format:
  client_ip<TAB>access_time<TAB>requested_file<TAB>response_type

5) Notes
- Each accepted TCP connection is handled by one thread.
- Supported statuses only:
  200 OK, 400 Bad Request, 403 Forbidden, 404 File Not Found, 304 Not Modified.
