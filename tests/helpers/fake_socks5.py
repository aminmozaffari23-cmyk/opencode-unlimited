#!/usr/bin/env python3
"""Tiny SOCKS5 server (no auth, CONNECT only) for testing the bridge.

It accepts a SOCKS5 handshake and connects to the requested destination, exactly
like WARP's proxy port does in proxy mode. Usage:

  python3 fake_socks5.py --port 14000
"""
import argparse
import socket
import struct
import threading


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("short read")
        buf += chunk
    return buf


def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(conn):
    try:
        ver, n = recv_exact(conn, 2)
        recv_exact(conn, n)
        conn.sendall(b"\x05\x00")
        ver, cmd, _, atyp = recv_exact(conn, 4)
        if atyp == 1:
            host = socket.inet_ntoa(recv_exact(conn, 4))
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(conn, 16))
        else:
            ln = recv_exact(conn, 1)[0]
            host = recv_exact(conn, ln).decode()
        port = struct.unpack("!H", recv_exact(conn, 2))[0]
        upstream = socket.create_connection((host, port), timeout=10)
        conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + b"\x00\x00")
        threading.Thread(target=pump, args=(upstream, conn), daemon=True).start()
        pump(conn, upstream)
    except Exception:
        try:
            conn.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", a.port))
    srv.listen(16)
    print("fake socks5 on %d" % a.port, flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
