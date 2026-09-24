"""SSH 本地端口转发 —— 把服务器上只绑 127.0.0.1 的服务映射到本机。

用途：服务器上的前端/后端只监听回环地址（刻意不对外暴露），
      通过这条隧道即可在浏览器里用 http://localhost:3000 访问，
      全程加密，且不需要开放任何服务器端口。

用法: SSH_PW='...' python tunnel.py
停止: Ctrl+C
"""
import os
import select
import socket
import sys
import threading
import time

import paramiko

HOST, USER = '106.54.224.27', 'root'
PW = os.environ.get('SSH_PW', '')

# (本地端口, 远端主机, 远端端口)
FORWARDS = [
    (3000, '127.0.0.1', 3000),   # 前端
    (8000, '127.0.0.1', 8000),   # 后端 API + /docs
]

if not PW:
    sys.exit('缺少 SSH_PW 环境变量')

transport = None
lock = threading.Lock()


def connect():
    global transport
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, 22, USER, password=PW, timeout=25,
                banner_timeout=25, auth_timeout=25,
                allow_agent=False, look_for_keys=False)
    with lock:
        transport = cli.get_transport()
    return cli


def pipe(a, b):
    """在两个 socket 之间双向搬运数据。"""
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r:
                continue
            if a in r:
                data = a.recv(8192)
                if not data:
                    break
                b.sendall(data)
            if b in r:
                data = b.recv(8192)
                if not data:
                    break
                a.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def serve(local_port, remote_host, remote_port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', local_port))
    srv.listen(128)
    print(f'  127.0.0.1:{local_port}  ->  {HOST}:{remote_port}', flush=True)
    while True:
        try:
            client, _ = srv.accept()
        except Exception:
            break
        try:
            with lock:
                t = transport
            if t is None or not t.is_active():
                client.close()
                continue
            chan = t.open_channel(
                'direct-tcpip', (remote_host, remote_port), client.getpeername()
            )
            threading.Thread(target=pipe, args=(client, chan), daemon=True).start()
        except Exception as e:
            print(f'  [转发失败] {type(e).__name__}: {e}', flush=True)
            client.close()


def main():
    print(f'连接 {USER}@{HOST} ...', flush=True)
    cli = connect()
    print('已连接，建立转发通道:\n', flush=True)
    for lp, rh, rp in FORWARDS:
        threading.Thread(target=serve, args=(lp, rh, rp), daemon=True).start()
    time.sleep(1)
    print('\n隧道已就绪，Ctrl+C 停止。', flush=True)
    try:
        while True:
            time.sleep(30)
            with lock:
                if transport is None or not transport.is_active():
                    print('连接已断开，重连中 ...', flush=True)
                    try:
                        cli.close()
                    except Exception:
                        pass
                    try:
                        cli = connect()
                        print('重连成功', flush=True)
                    except Exception as e:
                        print(f'重连失败: {e}', flush=True)
    except KeyboardInterrupt:
        print('\n正在关闭隧道 ...', flush=True)
    finally:
        try:
            cli.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
