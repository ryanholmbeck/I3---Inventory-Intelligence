"""
Indelco Local Server
Serves the HTML app + indelco.db from localhost:8765
Runs in background — no console window needed

Usage:
  python server.py          # start server (stays open)
  python server.py --stop   # stop server
"""

import http.server, socketserver, os, sys, threading, json
from pathlib import Path

PORT = 8765
BASE_DIR = Path(__file__).parent
DB_FILE  = BASE_DIR / 'indelco.db'
HIST_DB_FILE = BASE_DIR / 'indelco_historical.db'
HTML_FILE = BASE_DIR / 'Indelco_v3_Clean.html'

def _serve_db(handler, path):
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/octet-stream')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-cache')
    handler.end_headers()
    handler.wfile.write(data)

class IndelcoHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        # Serve indelco.db as binary
        if self.path == '/indelco.db':
            if not DB_FILE.exists():
                self.send_error(404, 'Database not built yet — run build_db.py first')
                return
            _serve_db(self, DB_FILE)
            return

        # Serve indelco_historical.db (static legacy snapshot) — 404 is OK if absent
        if self.path == '/indelco_historical.db':
            if not HIST_DB_FILE.exists():
                self.send_error(404, 'No historical database present — drop indelco_historical.db next to server.py to enable')
                return
            _serve_db(self, HIST_DB_FILE)
            return

        # Status endpoint
        if self.path == '/status':
            db_size = DB_FILE.stat().st_size if DB_FILE.exists() else 0
            hist_size = HIST_DB_FILE.stat().st_size if HIST_DB_FILE.exists() else 0
            status = {
                'db_exists': DB_FILE.exists(),
                'db_size_mb': round(db_size/1024/1024, 1),
                'db_modified': DB_FILE.stat().st_mtime if DB_FILE.exists() else None,
                'hist_db_exists': HIST_DB_FILE.exists(),
                'hist_db_size_mb': round(hist_size/1024/1024, 1),
                'hist_db_modified': HIST_DB_FILE.stat().st_mtime if HIST_DB_FILE.exists() else None,
            }
            data = json.dumps(status).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Suppress favicon noise
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        # Default: serve files from BASE_DIR
        super().do_GET()

    def log_message(self, format, *args):
        # Suppress request logging to keep it quiet
        try:
            if args and isinstance(args[0], str) and '/indelco.db' in args[0]:
                print(f'  DB served: {args[1]} {args[2]}')
        except Exception:
            pass

def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), IndelcoHandler) as httpd:
        print(f'Indelco server running at http://localhost:{PORT}')
        print(f'DB path: {DB_FILE}')
        if HIST_DB_FILE.exists():
            print(f'Historical DB: {HIST_DB_FILE}')
        print(f'Press Ctrl+C to stop\n')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')

if __name__ == '__main__':
    start_server()
