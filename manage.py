#!/usr/bin/env python3
"""
瑞幸咖啡空间分析平台 — 管理控制台
=====================================
一键管理 PostgreSQL、FastAPI 后端、Vite 前端、数据管道的启动/停止/状态检查。

用法:
    python manage.py                 交互式菜单模式
    python manage.py start           一键启动全部服务
    python manage.py stop            停止全部服务
    python manage.py restart         重启全部服务
    python manage.py status          查看所有服务运行状态
    python manage.py backend         仅启动后端
    python manage.py frontend        仅启动前端
    python manage.py setup           初始化数据库（建库/用户/PostGIS扩展/建表）
    python manage.py open            在浏览器打开前端页面
    python manage.py logs [service]  查看服务日志 (backend|frontend)

环境要求:
    - Windows 10/11 + Git Bash (或原生 Linux/macOS)
    - Python 3.11+
    - Node.js 20+
    - PostgreSQL 15 + PostGIS 3.6（Windows服务或Docker）
"""

import os
import sys
import signal
import socket
import json
import time
import subprocess
import webbrowser
import argparse
from pathlib import Path

# 强制 stdout 使用 UTF-8 编码（兼容 Windows Git Bash 的 GBK 默认编码）
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ─── 项目路径配置 ───────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
PID_DIR = PROJECT_ROOT / ".claude" / "pids"
PID_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_PID_FILE = PID_DIR / "backend.pid"
FRONTEND_PID_FILE = PID_DIR / "frontend.pid"
LOG_DIR = PID_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_LOG = LOG_DIR / "backend.log"
FRONTEND_LOG = LOG_DIR / "frontend.log"

# 从 .env 读取配置
def _load_env():
    """读取 .env 文件中的配置项，返回字典"""
    env = {}
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env

ENV = _load_env()
DB_HOST = ENV.get("POSTGRES_HOST", "localhost")
DB_PORT = ENV.get("POSTGRES_PORT", "5432")
DB_NAME = ENV.get("POSTGRES_DB", "luckin_spatial")
DB_USER = ENV.get("POSTGRES_USER", "luckin")
DB_PASS = ENV.get("POSTGRES_PASSWORD", "")
BACKEND_PORT = 8000
FRONTEND_PORT = 3000

# PostgreSQL 安装路径（Windows）
# 依次探测常见安装位置（C 盘默认、D 盘默认），命中哪一个用哪一个
_PG_BIN_CANDIDATES = [
    r"C:\Program Files\PostgreSQL\15\bin",
    r"D:\Program Files\PostgreSQL\15\bin",
]
PG_BIN = next(
    (p for p in _PG_BIN_CANDIDATES if Path(p, "psql.exe").is_file()),
    _PG_BIN_CANDIDATES[0],
)


# ══════════════════════════════════════════════════════════════
#  颜色工具
# ══════════════════════════════════════════════════════════════

class Color:
    """ANSI 终端颜色码，跨平台兼容 Git Bash / Linux / macOS 终端"""
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"


def _print(msg: str = ""):
    """print 的薄封装，确保 flush 立即输出"""
    print(msg, flush=True)


def success(msg: str):   _print(f"  {Color.GREEN}✓{Color.RESET} {msg}")
def fail(msg: str):      _print(f"  {Color.RED}✗{Color.RESET} {msg}")
def warn(msg: str):      _print(f"  {Color.YELLOW}⚠{Color.RESET} {msg}")
def info(msg: str):      _print(f"  {Color.CYAN}→{Color.RESET} {msg}")
def title(msg: str):     _print(f"\n{Color.BOLD}{Color.BLUE}{'='*50}\n  {msg}\n{'='*50}{Color.RESET}\n")
def heading(msg: str):   _print(f"\n{Color.BOLD}{Color.YELLOW}> {msg}{Color.RESET}")


# ══════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════

def check_port(port: int, host: str = "127.0.0.1") -> bool:
    """
    通过 TCP socket 连接，检测指定端口是否被监听。
    返回 True 表示端口已开放（有服务在监听）。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def wait_for_port(port: int, timeout: float = 15.0, host: str = "127.0.0.1") -> bool:
    """
    轮询等待端口可用，超时则返回 False。
    用于启动服务后等待其就绪。
    """
    start = time.time()
    while time.time() - start < timeout:
        if check_port(port, host):
            return True
        time.sleep(0.5)
    return False


def check_pg() -> bool:
    """
    检测 PostgreSQL 是否正在运行。
    使用两种方法：
      1. TCP 连接到 5432 端口
      2. pg_isready 命令（更可靠）
    """
    if not check_port(int(DB_PORT)):
        return False

    # 尝试 pg_isready
    pg_isready = os.path.join(PG_BIN, "pg_isready.exe") if os.name == "nt" else "pg_isready"
    try:
        result = subprocess.run(
            [pg_isready, "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER],
            capture_output=True, timeout=5,
            env={**os.environ, "PGPASSWORD": DB_PASS},
        )
        return result.returncode == 0
    except Exception:
        # pg_isready 不可用，端口检测已通过，视为运行中
        return True


def read_pid(pid_file: Path) -> int | None:
    """读取 PID 文件，返回进程号。文件不存在或内容无效返回 None"""
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def is_pid_alive(pid: int) -> bool:
    """
    检测指定 PID 的进程是否还在运行。
    Windows 使用 tasklist，Unix 使用 os.kill(pid, 0)。
    """
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def kill_pid(pid: int, force: bool = False):
    """
    终止指定 PID 的进程。
    Windows 使用 taskkill，Unix 使用 SIGTERM / SIGKILL。
    """
    if os.name == "nt":
        flag = "/F" if force else ""
        subprocess.run(["taskkill", "/PID", str(pid), flag],
                       capture_output=True, timeout=10)
    else:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def find_pid_by_port(port: int) -> int | None:
    """
    通过端口号查找占用该端口的进程 PID。
    Windows 使用 netstat，Unix 使用 lsof。
    返回 None 表示未找到。
    """
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid_str = parts[-1]
                    if pid_str.isdigit():
                        return int(pid_str)
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True, text=True, timeout=5,
            )
            pid_str = result.stdout.strip()
            if pid_str.isdigit():
                return int(pid_str)
        except Exception:
            pass
    return None


def stop_service_by_pid(pid_file: Path, name: str, port: int = 0):
    """
    通过 PID 文件安全停止一个服务。
    流程：读取 PID → 发送终止信号 → 等待退出 → 清理 PID 文件。
    如果 PID 文件不存在，则通过端口查找占用进程（netstat/lsof）。
    """
    pid = read_pid(pid_file)

    # PID 文件不存在 → 尝试通过端口查找
    if pid is None:
        if port > 0 and check_port(port):
            pid = find_pid_by_port(port)
            if pid:
                info(f"{name} 未由 manage.py 启动，但端口 {port} 被 PID {pid} 占用，尝试终止")
            else:
                info(f"{name} PID 文件不存在，端口 {port} 被占用但无法解析 PID")
                return
        else:
            info(f"{name} 未运行（无 PID 文件，端口未占用）")
            return

    if not is_pid_alive(pid):
        info(f"{name} (PID {pid}) 已退出，清理 PID 文件")
        pid_file.unlink(missing_ok=True)
        return

    info(f"正在停止 {name} (PID {pid})...")
    kill_pid(pid)

    # 等待进程退出，最多 5 秒
    for _ in range(10):
        if not is_pid_alive(pid):
            success(f"{name} 已停止")
            pid_file.unlink(missing_ok=True)
            return
        time.sleep(0.5)

    # 仍未退出则强制终止
    warn(f"{name} 未响应，强制终止...")
    kill_pid(pid, force=True)
    time.sleep(1)
    if not is_pid_alive(pid):
        success(f"{name} 已强制停止")
    else:
        fail(f"无法停止 {name} (PID {pid})")
    pid_file.unlink(missing_ok=True)


def start_subprocess(cmd: list[str], pid_file: Path, log_file: Path,
                     cwd: Path | None = None, name: str = "",
                     env: dict | None = None) -> subprocess.Popen:
    """
    在后台启动一个子进程，并将其 PID 写入 PID 文件。
    标准输出和标准错误重定向到日志文件。
    返回 Popen 对象。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fout = open(log_file, "a", encoding="utf-8")
    fout.write(f"\n{'='*60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {name} 启动\n{'='*60}\n")
    fout.flush()

    proc = subprocess.Popen(
        cmd,
        cwd=cwd or PROJECT_ROOT,
        stdout=fout,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env or os.environ,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    pid_file.write_text(str(proc.pid))
    return proc


def has_command(cmd: str) -> bool:
    """检测系统 PATH 中是否存在指定命令"""
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _find_npx() -> str | None:
    """
    查找 npx.cmd 的完整路径。
    依次搜索：项目内置便携 Node → Node.js 安装目录 → PATH → where 命令。
    返回 Windows 风格路径（如 C:\\Program Files\\nodejs\\npx.cmd），
    确保 subprocess 的 CreateProcess 能正确识别。
    """
    # 0. 优先使用项目内置的便携版 Node（离线部署包 deploy/node）
    bundled_npx = PROJECT_ROOT / "deploy" / "node" / "npx.cmd"
    if bundled_npx.is_file():
        return str(bundled_npx)

    # 1. 搜索已知的 Node.js 安装目录
    for base in [r"C:\Program Files\nodejs", r"C:\Program Files (x86)\nodejs"]:
        npx = os.path.join(base, "npx.cmd")
        if os.path.isfile(npx):
            return npx

    # 2. 搜索 PATH 中的 Windows 风格路径
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        npx = os.path.join(path_dir, "npx.cmd")
        if os.path.isfile(npx):
            return npx

    # 3. 使用 where 命令
    try:
        result = subprocess.run(["where", "npx"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        for line in lines:
            line = line.strip()
            if os.path.isfile(line):
                return line
    except Exception:
        pass

    return None


# ══════════════════════════════════════════════════════════════
#  各服务操作
# ══════════════════════════════════════════════════════════════

def check_prerequisites() -> bool:
    """检查运行所需的基础依赖：Python、Node.js、PostgreSQL"""
    heading("检查运行环境")

    ok = True

    # Python
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    success(f"Python {py_ver}")
    if sys.version_info < (3, 11):
        warn("  建议 Python 3.11+")

    # Node.js
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5)
        success(f"Node.js {result.stdout.strip()}")
    except Exception:
        fail("Node.js 未安装或不在 PATH 中")
        ok = False

    # npm / npx — 使用完整路径查找，兼容 Git Bash 的 Unix 风格 PATH
    npx_found = _find_npx()
    if npx_found:
        success(f"npx 可用 ({npx_found})")
    else:
        fail("npx 未找到，请确认 Node.js 已安装到 C:\\Program Files\\nodejs")
        ok = False

    # PostgreSQL
    if check_pg():
        success("PostgreSQL — 运行中")
    else:
        warn("PostgreSQL 未检测到运行中")
        warn("  请确保 PostgreSQL 15 已安装并启动")
        # 不标记为失败，因为可能是 Docker 方式

    # Python 关键包
    for pkg in ["fastapi", "uvicorn", "asyncpg", "aiohttp"]:
        try:
            __import__(pkg)
        except ImportError:
            warn(f"  Python 包 '{pkg}' 未安装")

    return ok


def start_backend() -> bool:
    """启动 FastAPI 后端，返回是否成功"""
    heading("启动后端 (FastAPI)")

    # 检查是否已在运行
    pid = read_pid(BACKEND_PID_FILE)
    if pid and is_pid_alive(pid):
        warn(f"后端已在运行 (PID {pid})")
        return True

    if check_port(BACKEND_PORT):
        warn(f"端口 {BACKEND_PORT} 已被占用，尝试停止旧进程...")
        stop_service_by_pid(BACKEND_PID_FILE, "后端")

    try:
        cmd = [
            sys.executable, "-m", "uvicorn", "backend.main:app",
            "--host", "0.0.0.0", "--port", str(BACKEND_PORT), "--reload",
        ]
        info(f"启动命令: {' '.join(cmd)}")
        start_subprocess(cmd, BACKEND_PID_FILE, BACKEND_LOG, name="Backend")

        # 等待就绪
        info(f"等待后端就绪 (端口 {BACKEND_PORT})...")
        if wait_for_port(BACKEND_PORT, timeout=20):
            success(f"后端已就绪 → http://localhost:{BACKEND_PORT}")
            success(f"API 文档     → http://localhost:{BACKEND_PORT}/docs")
            return True
        else:
            fail(f"后端启动超时（{BACKEND_PORT} 端口未响应）")
            return False
    except Exception as e:
        fail(f"后端启动失败: {e}")
        return False


def start_frontend() -> bool:
    """启动 Vite 前端，返回是否成功"""
    heading("启动前端 (Vite)")

    pid = read_pid(FRONTEND_PID_FILE)
    if pid and is_pid_alive(pid):
        warn(f"前端已在运行 (PID {pid})")
        return True

    if check_port(FRONTEND_PORT):
        warn(f"端口 {FRONTEND_PORT} 已被占用，尝试停止旧进程...")
        stop_service_by_pid(FRONTEND_PID_FILE, "前端")

    frontend_dir = PROJECT_ROOT / "frontend"
    if not (frontend_dir / "node_modules").exists():
        warn("node_modules 未安装，正在运行 npm install...")
        subprocess.run(["npm", "install"], cwd=frontend_dir, timeout=120)

    # 查找 npx.cmd 的完整路径（Git Bash 的 Unix 风格 PATH 不被 Windows CreateProcess 识别）
    npx_path = _find_npx()
    if not npx_path:
        fail("未找到 npx，请确认 Node.js 已安装")
        return False

    try:
        info(f"启动前端开发服务器 (npx={npx_path})...")
        start_subprocess(
            [npx_path, "vite", "--host"],
            FRONTEND_PID_FILE, FRONTEND_LOG,
            cwd=frontend_dir, name="Frontend",
        )

        info(f"等待前端就绪 (端口 {FRONTEND_PORT})...")
        if wait_for_port(FRONTEND_PORT, timeout=20):
            success(f"前端已就绪 → http://localhost:{FRONTEND_PORT}")
            return True
        else:
            fail(f"前端启动超时（{FRONTEND_PORT} 端口未响应）")
            return False
    except Exception as e:
        fail(f"前端启动失败: {e}")
        return False


def stop_backend():
    """停止后端服务（先尝试 PID 文件，再尝试端口查找，最后二次清理残留）"""
    heading("停止后端")
    stop_service_by_pid(BACKEND_PID_FILE, "后端", port=BACKEND_PORT)
    # 二次清理：uvicorn --reload 会启动两个进程（主进程+文件监听器），
    # 主进程被 kill 后监听器可能仍持有端口
    time.sleep(1)
    if check_port(BACKEND_PORT):
        warn(f"端口 {BACKEND_PORT} 仍被占用，尝试二次清理...")
        leftover_pid = find_pid_by_port(BACKEND_PORT)
        if leftover_pid:
            info(f"发现残留进程 PID {leftover_pid}，强制终止")
            kill_pid(leftover_pid, force=True)
            time.sleep(1)
    if check_port(BACKEND_PORT):
        warn(f"端口 {BACKEND_PORT} 仍被占用，请手动处理")
    else:
        success(f"端口 {BACKEND_PORT} 已释放")


def stop_frontend():
    """停止前端服务（先尝试 PID 文件，再尝试端口查找，最后二次清理残留）"""
    heading("停止前端")
    stop_service_by_pid(FRONTEND_PID_FILE, "前端", port=FRONTEND_PORT)
    # 二次清理：vite 也可能有残留子进程
    time.sleep(1)
    if check_port(FRONTEND_PORT):
        warn(f"端口 {FRONTEND_PORT} 仍被占用，尝试二次清理...")
        leftover_pid = find_pid_by_port(FRONTEND_PORT)
        if leftover_pid:
            info(f"发现残留进程 PID {leftover_pid}，强制终止")
            kill_pid(leftover_pid, force=True)
            time.sleep(1)
    if check_port(FRONTEND_PORT):
        warn(f"端口 {FRONTEND_PORT} 仍被占用，请手动处理")
    else:
        success(f"端口 {FRONTEND_PORT} 已释放")


def show_status():
    """打印所有服务的运行状态摘要"""
    heading("服务状态检测")

    # PostgreSQL
    _print()
    if check_pg():
        success(f"PostgreSQL  {Color.DIM}({DB_HOST}:{DB_PORT}){Color.RESET}")
    else:
        fail(f"PostgreSQL  {Color.DIM}({DB_HOST}:{DB_PORT}){Color.RESET} — 未运行")

    # 后端
    pid = read_pid(BACKEND_PID_FILE)
    alive = is_pid_alive(pid) if pid else False
    port_open = check_port(BACKEND_PORT)
    if alive and port_open:
        success(f"后端 (FastAPI)  {Color.DIM}PID {pid} | :{BACKEND_PORT} | /docs{Color.RESET}")
    elif port_open:
        warn(f"后端 (FastAPI)  {Color.DIM}端口 {BACKEND_PORT} 已占用但 PID 不匹配{Color.RESET}")
    elif pid:
        fail(f"后端 (FastAPI)  {Color.DIM}PID {pid} 存在但未响应{Color.RESET}")
    else:
        _print(f"  {Color.DIM}后端 (FastAPI) — 未启动{Color.RESET}")

    # 前端
    pid = read_pid(FRONTEND_PID_FILE)
    alive = is_pid_alive(pid) if pid else False
    port_open = check_port(FRONTEND_PORT)
    if alive and port_open:
        success(f"前端 (Vite)    {Color.DIM}PID {pid} | :{FRONTEND_PORT}{Color.RESET}")
    elif port_open:
        warn(f"前端 (Vite)    {Color.DIM}端口 {FRONTEND_PORT} 已占用但 PID 不匹配{Color.RESET}")
    elif pid:
        fail(f"前端 (Vite)    {Color.DIM}PID {pid} 存在但未响应{Color.RESET}")
    else:
        _print(f"  {Color.DIM}前端 (Vite) — 未启动{Color.RESET}")

    _print()
    # 快捷链接
    if check_port(BACKEND_PORT):
        _print(f"  {Color.CYAN}后端健康检查:{Color.RESET} http://localhost:{BACKEND_PORT}/health")
        _print(f"  {Color.CYAN}API 文档:{Color.RESET}     http://localhost:{BACKEND_PORT}/docs")
    if check_port(FRONTEND_PORT):
        _print(f"  {Color.CYAN}前端页面:{Color.RESET}     http://localhost:{FRONTEND_PORT}")
    _print()


def setup_database():
    """
    初始化数据库：创建用户 → 创建数据库 → 启用 PostGIS 扩展 → 建表。
    仅执行缺失的步骤，已存在的会跳过。
    """
    heading("初始化数据库")

    if not check_pg():
        fail("PostgreSQL 未运行，请先启动数据库")
        return False

    pg_isready = os.path.join(PG_BIN, "pg_isready.exe") if os.name == "nt" else "pg_isready"
    psql = os.path.join(PG_BIN, "psql.exe") if os.name == "nt" else "psql"
    env = {**os.environ, "PGPASSWORD": DB_PASS}

    def run_sql(sql: str, db: str = "postgres", user: str = "postgres"):
        """以指定用户连接到指定数据库，执行一条 SQL 语句"""
        result = subprocess.run(
            [psql, "-U", user, "-h", DB_HOST, "-p", DB_PORT, "-d", db,
             "-c", sql],
            capture_output=True, text=True, timeout=15, env=env,
        )
        return result

    # 1. 检查并创建用户
    info(f"检查数据库用户 '{DB_USER}'...")
    r = subprocess.run(
        [psql, "-U", "postgres", "-h", DB_HOST, "-p", DB_PORT, "-tAc",
         f"SELECT 1 FROM pg_roles WHERE rolname='{DB_USER}'"],
        capture_output=True, text=True, timeout=10, env=env,
    )
    if "1" not in r.stdout:
        info(f"创建用户 '{DB_USER}'...")
        r = run_sql(f"CREATE USER {DB_USER} WITH PASSWORD '{DB_PASS}'")
        if r.returncode == 0:
            success(f"用户 '{DB_USER}' 创建成功")
        else:
            fail(f"用户创建失败: {r.stderr}")
            return False
    else:
        success(f"用户 '{DB_USER}' 已存在")

    # 2. 检查并创建数据库
    info(f"检查数据库 '{DB_NAME}'...")
    r = subprocess.run(
        [psql, "-U", "postgres", "-h", DB_HOST, "-p", DB_PORT, "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname='{DB_NAME}'"],
        capture_output=True, text=True, timeout=10, env=env,
    )
    if "1" not in r.stdout:
        info(f"创建数据库 '{DB_NAME}'...")
        r = run_sql(f"CREATE DATABASE {DB_NAME} OWNER {DB_USER}")
        if r.returncode == 0:
            success(f"数据库 '{DB_NAME}' 创建成功")
        else:
            fail(f"数据库创建失败: {r.stderr}")
            return False
    else:
        success(f"数据库 '{DB_NAME}' 已存在")

    # 授予权限
    run_sql(f"GRANT ALL PRIVILEGES ON DATABASE {DB_NAME} TO {DB_USER}")
    run_sql(f"GRANT ALL ON SCHEMA public TO {DB_USER}", db=DB_NAME)

    # 3. 启用 PostGIS 扩展
    info("启用 PostGIS 扩展...")
    for ext in ["postgis", "postgis_topology", "postgis_raster"]:
        r = run_sql(f"CREATE EXTENSION IF NOT EXISTS {ext}", db=DB_NAME, user="postgres")
        if r.returncode == 0:
            success(f"  {ext}")
        elif "already exists" in r.stderr.lower():
            success(f"  {ext} (已存在)")
        else:
            warn(f"  {ext}: {r.stderr.strip()}")

    # 4. 运行 SQL 初始化脚本
    info("执行建表脚本...")
    for sql_file in ["schema.sql", "spatial_join.sql", "analytics.sql"]:
        path = PROJECT_ROOT / "sql" / sql_file
        if path.exists():
            r = subprocess.run(
                [psql, "-U", DB_USER, "-h", DB_HOST, "-p", DB_PORT, "-d", DB_NAME,
                 "-f", str(path)],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if r.returncode == 0:
                success(f"  {sql_file}")
            else:
                first_error = r.stderr.strip().split('\n')[-1] if r.stderr.strip() else "未知错误"
                warn(f"  {sql_file}: {first_error}")
        else:
            fail(f"  {sql_file} — 文件不存在")

    _print()
    success("数据库初始化完成")
    return True


def start_all():
    """一键启动全部服务"""
    title("🍵 瑞幸空间分析平台 — 一键启动")

    check_prerequisites()

    # 确保 PostgreSQL 在运行
    if not check_pg():
        heading("启动 PostgreSQL")
        if os.name == "nt":
            info("尝试启动 PostgreSQL Windows 服务...")
            r = subprocess.run(["net", "start", "postgresql-x64-15"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                success("PostgreSQL 服务已启动")
                time.sleep(2)
            else:
                warn("无法启动 PostgreSQL Windows 服务")
                warn(f"请手动启动: net start postgresql-x64-15")
        else:
            warn("请手动启动 PostgreSQL")

    if not check_pg():
        fail("PostgreSQL 未运行，无法继续")
        return

    # 启动后端，然后前端
    backend_ok = start_backend()
    frontend_ok = start_frontend()

    # 汇总
    _print(f"\n{Color.BOLD}{'='*50}{Color.RESET}")
    _print(f"{Color.BOLD}  服务状态总览{Color.RESET}")
    _print(f"{'='*50}")
    _print(f"  PostgreSQL  {Color.GREEN}● 运行中{Color.RESET}    {DB_HOST}:{DB_PORT}")
    if backend_ok:
        _print(f"  后端 (API)  {Color.GREEN}● 运行中{Color.RESET}    http://localhost:{BACKEND_PORT}")
    else:
        _print(f"  后端 (API)  {Color.RED}● 启动失败{Color.RESET}")
    if frontend_ok:
        _print(f"  前端 (Web)  {Color.GREEN}● 运行中{Color.RESET}    http://localhost:{FRONTEND_PORT}")
    else:
        _print(f"  前端 (Web)  {Color.RED}● 启动失败{Color.RESET}")
    _print(f"{'='*50}\n")


def stop_all():
    """停止全部服务（后端+前端），PostgreSQL 保持不变"""
    title("🛑 停止全部服务")
    stop_frontend()
    stop_backend()
    _print()
    success("所有服务已停止 (PostgreSQL 未受影响)")


def restart_all():
    """重启全部服务"""
    title("🔄 重启全部服务")
    stop_frontend()
    stop_backend()
    _print()
    time.sleep(2)
    start_all()


def open_browser():
    """在默认浏览器打开前端页面"""
    url = f"http://localhost:{FRONTEND_PORT}"
    if check_port(FRONTEND_PORT):
        info(f"打开 {url}")
        webbrowser.open(url)
        success("已在浏览器打开前端页面")
    else:
        fail("前端未运行，请先执行 python manage.py start 或 python manage.py frontend")


def show_logs(service: str = "backend", lines: int = 50):
    """显示最近的服务日志"""
    log_file = BACKEND_LOG if service == "backend" else FRONTEND_LOG
    if not log_file.exists():
        warn(f"{service} 日志文件不存在")
        return

    content = log_file.read_text(encoding="utf-8", errors="replace")
    tail_lines = content.split("\n")[-lines:]
    heading(f"{service} 日志 (最近 {lines} 行)")
    _print("\n".join(tail_lines))


# ══════════════════════════════════════════════════════════════
#  交互式菜单
# ══════════════════════════════════════════════════════════════

MENU_ITEMS = [
    ("🚀 一键启动全部服务", start_all),
    ("🛑 停止全部服务",     stop_all),
    ("🔄 重启全部服务",     restart_all),
    ("📊 查看服务状态",     show_status),
    ("🗄️  初始化数据库",    setup_database),
    ("📡 仅启动后端",       start_backend),
    ("🎨 仅启动前端",       start_frontend),
    ("📝 查看后端日志",     lambda: show_logs("backend")),
    ("📝 查看前端日志",     lambda: show_logs("frontend")),
    ("🌐 打开前端页面",     open_browser),
]

MENU_WIDTH = 52


def print_banner():
    """打印横幅和交互式菜单"""
    _print(f"\n{Color.CYAN}{Color.BOLD}")
    _print("╔" + "═" * (MENU_WIDTH - 2) + "╗")
    _print("║" + "  🍵 瑞幸空间分析平台 — 管理控制台".ljust(MENU_WIDTH - 4) + "║")
    _print("╠" + "═" * (MENU_WIDTH - 2) + "╣")

    for i, (label, _) in enumerate(MENU_ITEMS):
        num = f"[{i + 1}]" if i < 9 else "[0]"
        line = f"  {num} {label}"
        _print("║" + line.ljust(MENU_WIDTH - 4) + "║")

    _print("║" + f"  [q] ❌ 退出".ljust(MENU_WIDTH - 4) + "║")
    _print("╚" + "═" * (MENU_WIDTH - 2) + "╝")
    _print(f"{Color.RESET}")
    _print(f"  {Color.DIM}输入数字选择操作，或 Ctrl+C 退出{Color.RESET}")
    _print()


def interactive_mode():
    """交互式菜单主循环"""
    print_banner()

    while True:
        try:
            choice = input(f"{Color.BOLD}  请输入选项 > {Color.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            _print("\n")
            break

        if not choice:
            continue

        if choice.lower() in ("q", "quit", "exit", "0"):
            _print(f"\n  {Color.DIM}再见！{Color.RESET}\n")
            break

        try:
            idx = int(choice) - 1
        except ValueError:
            warn(f"无效输入: '{choice}'，请输入数字 1-{len(MENU_ITEMS)} 或 q 退出")
            continue

        if 0 <= idx < len(MENU_ITEMS):
            _, action = MENU_ITEMS[idx]
            try:
                action()
            except KeyboardInterrupt:
                _print(f"\n  {Color.YELLOW}操作已取消{Color.RESET}\n")
            except Exception as e:
                fail(f"操作异常: {e}")
        else:
            warn(f"选项 {idx + 1} 不在范围内 (1-{len(MENU_ITEMS)})")


# ══════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="瑞幸空间分析平台 — 管理控制台",
        usage="python manage.py [命令]",
    )
    parser.add_argument("command", nargs="?", default=None,
                        choices=["start", "stop", "restart", "status",
                                 "backend", "frontend", "setup", "open", "logs"])
    parser.add_argument("args", nargs="*", help="额外参数（如 logs backend）")

    args = parser.parse_args()

    if args.command is None:
        # 无参数 → 交互式菜单
        interactive_mode()
        return

    # 命令模式
    match args.command:
        case "start":
            start_all()
        case "stop":
            stop_all()
        case "restart":
            restart_all()
        case "status":
            show_status()
        case "backend":
            check_prerequisites()
            start_backend()
        case "frontend":
            check_prerequisites()
            start_frontend()
        case "setup":
            setup_database()
        case "open":
            open_browser()
        case "logs":
            service = args.args[0] if args.args else "backend"
            if service not in ("backend", "frontend"):
                fail("请指定 backend 或 frontend")
            else:
                show_logs(service)
        case _:
            parser.print_help()


if __name__ == "__main__":
    main()
