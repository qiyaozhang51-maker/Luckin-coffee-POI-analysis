"""把项目部署到 106.54.224.27 —— 只在本机验证，不对外暴露。

设计原则（针对这台正在跑生产业务的服务器）：
  - 独立项目名 luckin-spatial，不碰 dataease20 / nginx / 1panel
  - 所有端口只绑 127.0.0.1，不改 UFW、不改安全组
  - PostGIS 不发布任何端口
  - 全部文件放在 /opt/luckin-spatial，卸载即 rm -rf

用法: SSH_PW='...' python deploy.py [--skip-upload] [--skip-build]
"""
import io
import os
import posixpath
import sys
import tarfile
import time
from pathlib import Path

import paramiko

HOST, USER = '106.54.224.27', 'root'
PW = os.environ.get('SSH_PW', '')
REMOTE_DIR = '/opt/luckin-spatial'
PROJECT = 'luckin-spatial'

ROOT = Path(__file__).resolve().parents[2]   # 项目根目录
HERE = Path(__file__).resolve().parent       # deploy/server

if not PW:
    sys.exit('缺少 SSH_PW 环境变量')

flags = set(sys.argv[1:])


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ---------------------------------------------------------------- 打包
# 排除：虚拟环境、依赖目录、缓存、原始大文件（数据已在备份里）
EXCLUDE_DIRS = {'node_modules', '.venv', '__pycache__', '.git', 'dist', '.claude'}
EXCLUDE_FILES = {'package-lock.json'}

INCLUDE = [
    'backend', 'frontend', 'scripts', 'sql',
    'requirements-api.txt', '.env',
]
INCLUDE_FILES = [
    'data/models/xgboost_location_model.pkl',
    'data/luckin_spatial_backup.sql',
]
# 服务器版部署文件（本地在 deploy/server/，上传后按 dst 路径放到位）
DOCKERFILES = {
    'backend.Dockerfile.server': 'backend/Dockerfile.server',
    'frontend.Dockerfile.server': 'frontend/Dockerfile.server',
    'docker-compose.server.yml': 'docker-compose.server.yml',
}


def build_tar() -> io.BytesIO:
    buf = io.BytesIO()
    n, total = 0, 0
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for item in INCLUDE:
            p = ROOT / item
            if not p.exists():
                log(f'  ! 跳过不存在的 {item}')
                continue
            if p.is_file():
                tar.add(p, arcname=item)
                n += 1
                continue
            for f in p.rglob('*'):
                if not f.is_file():
                    continue
                if any(part in EXCLUDE_DIRS for part in f.parts):
                    continue
                if f.name in EXCLUDE_FILES:
                    continue
                tar.add(f, arcname=str(f.relative_to(ROOT)).replace('\\', '/'))
                n += 1
        for item in INCLUDE_FILES:
            p = ROOT / item
            if p.exists():
                tar.add(p, arcname=item)
                n += 1
            else:
                log(f'  ! 缺少关键文件 {item}')
        for src in DOCKERFILES:
            tar.add(HERE / src, arcname=f'_server_dockerfiles/{src}')
            n += 1
    total = buf.tell()
    log(f'打包完成: {n} 个文件, {total / 1024 / 1024:.1f} MB')
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- 执行
def run(cli, cmd, timeout=1800, stream=False):
    """执行远端命令并返回 (exit_code, output)。"""
    _, out, err = cli.exec_command(cmd, timeout=timeout, get_pty=False)
    o = out.read().decode('utf-8', 'replace')
    e = err.read().decode('utf-8', 'replace')
    rc = out.channel.recv_exit_status()
    if stream and o:
        for line in o.splitlines():
            print('   |', line, flush=True)
    return rc, (o + e)


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log(f'连接 {USER}@{HOST} ...')
    cli.connect(HOST, 22, USER, password=PW, timeout=25,
                banner_timeout=25, auth_timeout=25,
                allow_agent=False, look_for_keys=False)
    log('已连接')

    # ---------- 1. 上传 ----------
    if '--skip-upload' not in flags:
        tar_bytes = build_tar()
        run(cli, f'mkdir -p {REMOTE_DIR}')
        log('上传中（含 34MB 备份，请稍候）...')
        sftp = cli.open_sftp()
        sftp.putfo(tar_bytes, f'{REMOTE_DIR}/_deploy.tar.gz')
        sftp.close()
        log('上传完成，解压中...')
        rc, o = run(cli, f'cd {REMOTE_DIR} && tar xzf _deploy.tar.gz && rm -f _deploy.tar.gz && echo OK')
        if rc != 0:
            log(f'解压失败:\n{o}')
            sys.exit(1)
        # 把服务器版 Dockerfile 放到位
        for src, dst in DOCKERFILES.items():
            run(cli, f'cd {REMOTE_DIR} && cp _server_dockerfiles/{src} {dst}')
        run(cli, f'cd {REMOTE_DIR} && rm -rf _server_dockerfiles')
        log('文件就位')

        # 关键文件校验
        rc, o = run(cli, f'cd {REMOTE_DIR} && ls -lh data/luckin_spatial_backup.sql '
                         f'data/models/xgboost_location_model.pkl backend/Dockerfile.server '
                         f'frontend/Dockerfile.server docker-compose.server.yml 2>&1')
        log('关键文件:\n' + o.strip())

    # ---------- 2. 构建 ----------
    log('开始构建镜像（npm install + pip install，2 核机器请耐心）...')
    rc, o = run(
        cli,
        f'cd {REMOTE_DIR} && set -o pipefail; docker compose --progress plain '
        f'-f docker-compose.server.yml -p {PROJECT} build 2>&1 | tail -40',
        timeout=2400, stream=True,
    )
    if rc != 0:
        log(f'构建失败 (exit {rc})')
        log(o[-3000:])
        sys.exit(1)
    log('构建完成')

    # ---------- 3. 启动 ----------
    log('启动服务...')
    rc, o = run(
        cli,
        f'cd {REMOTE_DIR} && docker compose -f docker-compose.server.yml -p {PROJECT} up -d 2>&1',
        timeout=600,
    )
    log(o.strip())
    if rc != 0:
        log(f'启动失败 (exit {rc})')
        sys.exit(1)

    # ---------- 4. 等待数据库导入完成 ----------
    log('等待 PostGIS 就绪并导入备份（34MB，可能需要 1-3 分钟）...')
    for i in range(40):
        rc, o = run(
            cli,
            f'docker exec luckin-postgis psql -U luckin -d luckin_spatial -tAc '
            f'"SELECT count(*) FROM stores" 2>&1',
            timeout=60,
        )
        val = o.strip().splitlines()[-1] if o.strip() else ''
        if rc == 0 and val.isdigit() and int(val) > 0:
            log(f'数据已导入: stores 表 {val} 行')
            break
        time.sleep(10)
    else:
        log('警告: 等待数据导入超时，继续检查服务状态')

    # ---------- 4b. 刷新物化视图 ----------
    # pg_dump 默认不导出物化视图数据，恢复后视图处于 "not populated" 状态，
    # 任何查询都会报 ObjectNotInPrerequisiteStateError。必须显式 REFRESH。
    log('刷新物化视图 ...')
    rc, o = run(
        cli,
        'docker exec luckin-postgis psql -U luckin -d luckin_spatial -tAc '
        '"SELECT matviewname FROM pg_matviews WHERE NOT ispopulated"',
        timeout=60,
    )
    pending = [v.strip() for v in o.strip().splitlines() if v.strip()]
    if pending:
        for mv in pending:
            rc2, o2 = run(
                cli,
                f'docker exec luckin-postgis psql -U luckin -d luckin_spatial -c '
                f'"REFRESH MATERIALIZED VIEW {mv}" 2>&1',
                timeout=300,
            )
            log(f'  REFRESH {mv}: {"OK" if rc2 == 0 else o2.strip()}')
    else:
        log('  无需刷新')

    # ---------- 5. 验证 ----------
    log('验证服务状态 ...')
    rc, o = run(cli, f'cd {REMOTE_DIR} && docker compose -f docker-compose.server.yml -p {PROJECT} ps 2>&1')
    log(o.strip())

    log('探测各端口 ...')
    checks = [
        ('后端 /health',  'curl -s -m 10 http://127.0.0.1:8000/health'),
        ('门店总数 API',  'curl -s -m 15 "http://127.0.0.1:8000/api/stores?limit=1" | head -c 300'),
        ('前端首页',      'curl -s -o /dev/null -w "%{http_code}" -m 10 http://127.0.0.1:3000/'),
        ('数据库统计',    'docker exec luckin-postgis psql -U luckin -d luckin_spatial -tAc '
                          '"SELECT brand, count(*) FROM stores GROUP BY brand"'),
        ('数据表行数',    'docker exec luckin-postgis psql -U luckin -d luckin_spatial -tAc '
                          '"SELECT (SELECT count(*) FROM pois) AS pois, '
                          '(SELECT count(*) FROM store_poi_stats) AS sps"'),
    ]
    for title, cmd in checks:
        rc, o = run(cli, cmd, timeout=90)
        log(f'{title}: {o.strip()[:200]}')

    log('确认未对外暴露 ...')
    rc, o = run(cli, 'ss -tlnp | grep -E ":(3000|8000|5432)\\b" || echo "未监听（符合预期）"')
    log(o.strip()[:400])

    cli.close()
    log('部署流程结束')


if __name__ == '__main__':
    main()
