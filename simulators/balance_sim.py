"""TCP server that streams a fake balance reading once per second to whoever connects."""
import random
import socketserver
import sys
import time


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = f"S-{random.randint(1, 3):03d},{random.uniform(1, 50):.3f},g\n"
            self.wfile.write(line.encode())
            time.sleep(1)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as srv:
        print("balance simulator on", port)
        srv.serve_forever()
