"""把前端发布到公网 —— 新增独立端口的 nginx server 块，不动现有站点。

安全设计：
  - 新配置文件 /etc/nginx/conf.d/luckin-8080.conf，监听 8080，与 80/443/8090 完全隔离
  - reload 前先 nginx -t，测试不通过则不改动、不 reload
  - 只新增文件，不修改任何现有配置

用法: SSH_PW='...' python publish.py
"""
import os
import sys
from pathlib import Path

import paramiko

HOST, USER = '106.54.224.27', 'root'
PW = os.environ.get('SSH_PW', '')
REMOTE = '/opt/luckin-spatial'
PORT = 8080
CONF = '/etc/nginx/conf.d/luckin-8080.conf'

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'frontend' / 'dist'

if not PW:
    sys.exit('缺少 SSH_PW 环境变量')
if not DIST.exists():
    sys.exit(f'未找到构建产物 {DIST}，请先在 frontend/ 下执行 npm run build')

NGINX_CONF = f"""# 瑞幸空间分析平台 —— 公网访问入口
# 由 deploy/server/publish.py 生成。
#
# 这是一个新增的独立 server 块，监听 8080 端口，
# 不影响 80 / 443 / 8090 上的现有站点（DataEase、xzllove.online、智能体代理）。
#
# 卸载：删除本文件后 nginx -s reload

server {{
    listen {PORT};
    server_name _;

    root {REMOTE}/frontend/dist;
    index index.html;

    # 前端为 React SPA，未匹配的路径回退到 index.html 以支持前端路由
    location / {{
        try_files $uri $uri/ /index.html;
    }}

    # API 反向代理到后端容器（后端只绑 127.0.0.1:8000，此处同机转发）
    location /api/ {{
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 30s;
    }}

    # 构建产物带内容哈希，可长期缓存
    location /assets/ {{
        expires 7d;
        add_header Cache-Control "public, immutable";
    }}

    # gzip —— 热力图接口返回的 GeoJSON 体积大，压缩收益明显
    gzip on;
    gzip_types text/plain text/css application/javascript application/json
               application/geo+json image/svg+xml;
    gzip_min_length 1024;
    gzip_comp_level 5;

    client_max_body_size 10m;
}}
"""


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, 22, USER, password=PW, timeout=25,
                banner_timeout=25, auth_timeout=25,
                allow_agent=False, look_for_keys=False)

    def run(c, t=120):
        _, o, e = cli.exec_command(c, timeout=t)
        return o.channel.recv_exit_status(), (o.read() + e.read()).decode('utf-8', 'replace').strip()

    # ---------- 1. 上传构建产物 ----------
    print('[1/5] 上传前端构建产物 ...', flush=True)
    sftp = cli.open_sftp()
    run(f'rm -rf {REMOTE}/frontend/dist && mkdir -p {REMOTE}/frontend/dist/assets')
    for f in sorted(DIST.rglob('*')):
        if f.is_file():
            rel = f.relative_to(DIST).as_posix()
            sftp.put(str(f), f'{REMOTE}/frontend/dist/{rel}')
    # 同步修好的源码，保证服务器上也能自行重建
    sftp.put(str(ROOT / 'frontend/src/components/MapView.tsx'),
             f'{REMOTE}/frontend/src/components/MapView.tsx')
    sftp.close()
    rc, o = run(f'ls -la {REMOTE}/frontend/dist/ && du -sh {REMOTE}/frontend/dist')
    print(o, flush=True)

    # ---------- 2. 写入 nginx 配置 ----------
    print(f'\n[2/5] 写入 nginx 配置 {CONF} ...', flush=True)
    sftp = cli.open_sftp()
    with sftp.file(CONF, 'w') as fh:
        fh.write(NGINX_CONF)
    sftp.close()
    print('已写入（新文件，未修改任何现有配置）', flush=True)

    # ---------- 3. 测试配置 ----------
    print('\n[3/5] nginx 配置测试 ...', flush=True)
    rc, o = run('nginx -t 2>&1')
    print(o, flush=True)
    if rc != 0:
        print('配置测试失败，已回滚（删除新配置文件），未 reload', flush=True)
        run(f'rm -f {CONF}')
        sys.exit(1)
    print('测试通过', flush=True)

    # ---------- 4. reload ----------
    print('\n[4/5] reload nginx ...', flush=True)
    rc, o = run('nginx -s reload 2>&1 && echo reloaded')
    print(o, flush=True)
    if rc != 0:
        print('reload 失败', flush=True)
        sys.exit(1)

    # ---------- 5. 验证 ----------
    print('\n[5/5] 验证 ...', flush=True)
    rc, o = run('ufw status | grep 8080 || echo "UFW 未显式放行 8080（可能是 Anywhere 规则）"')
    print('UFW:', o, flush=True)
    for title, cmd in [
        ('本机 8080 首页', f'curl -s -o /dev/null -w "%{{http_code}}" -m 10 http://127.0.0.1:{PORT}/'),
        ('本机 8080 API 代理',
         f'curl -s -o /dev/null -w "%{{http_code}}" -m 15 http://127.0.0.1:{PORT}/api/stores/cities/list'),
        ('gzip 是否生效',
         f'curl -s -H "Accept-Encoding: gzip" -o /dev/null -w "%{{size_download}}" -m 20 '
         f'http://127.0.0.1:{PORT}/api/stores/heatmap/data?brand=luckin'),
        ('监听状态', f'ss -tln | grep ":{PORT} " || echo "未监听"'),
    ]:
        rc, o = run(cmd, 60)
        print(f'  {title}: {o}', flush=True)

    # 现有站点是否仍正常
    print('\n  现有站点回归检查:', flush=True)
    for title, cmd in [
        ('nginx 服务', 'systemctl is-active nginx'),
        ('80 端口默认站', 'curl -s -o /dev/null -w "%{http_code}" -m 10 http://127.0.0.1:80/'),
        ('8090 端口', 'curl -s -o /dev/null -w "%{http_code}" -m 10 http://127.0.0.1:8090/'),
    ]:
        rc, o = run(cmd, 30)
        print(f'    {title}: {o}', flush=True)

    cli.close()
    print(f'\n完成。公网地址: http://{HOST}:{PORT}/', flush=True)


if __name__ == '__main__':
    main()
