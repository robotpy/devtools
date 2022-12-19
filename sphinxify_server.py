#!/usr/bin/env python3

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import webbrowser

import sphinxify


index_html = b"""
<!DOCTYPE html>
<html>
    <head></head>
    <body>
    In: doxygen contents<br/>
    <textarea id="inbox" rows="10" cols="100"></textarea><br/>
    Out: python docstring<br/>
    <textarea id="outbox" rows="10" cols="100"></textarea>

    <script>

    function xfer() {
        let data = {inbox: inbox.value}
        fetch("/sphinxify", {
            method: "POST",
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        }).then(res => {
            res.json().then(res => {
                outbox.value = res.outbox;
            })
        });
    }

    inbox.oninput = inbox.onpropertychange = inbox.onpaste = xfer;
    xfer();

    </script>
</html>
"""


class SphinxifyAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(index_html)))
            self.end_headers()
            self.wfile.write(index_html)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/sphinxify":
            length = int(self.headers["content-length"])
            postdata = json.loads(self.rfile.read(length).decode("utf-8"))
            result = sphinxify.process(postdata["inbox"])
            response = json.dumps(dict(outbox=result))

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.wfile.write(response.encode("utf-8"))
        else:
            self.send_error(404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", type=int, default=5678)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), SphinxifyAPI)
    url = f"http://127.0.0.1:{args.port}/"
    print("Sphinxify server listening at", url)

    webbrowser.open(url)

    server.serve_forever()
