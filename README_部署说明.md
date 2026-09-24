# 🍵 瑞幸空间分析平台 — 离线部署包使用说明

> 本压缩包是**完整离线部署包**，已包含全部源码、数据、前端依赖（node_modules）、
> 后端 Python 依赖（离线 wheel）、便携版 Node.js 和 PostGIS 捆绑包。
> 目标电脑**无需联网**即可完成部署（仅需自备 Python 3.11 和 PostgreSQL 15 两样基础环境）。

---

## 一、压缩包内容清单

```
luckin-spatial-analysis/
├── 安装部署.bat                 ← ① 双击执行：装依赖 + 初始化数据库
├── 启动服务.bat                 ← ② 双击执行：一键启动全部服务
├── 停止服务.bat                 ←    双击执行：停止全部服务
├── README_部署说明.md            ← 本文档
├── manage.py                   ← 服务管理控制台
├── backend/                    ← FastAPI 后端源码
├── frontend/                   ← React + deck.gl 前端源码（含 node_modules，离线可用）
├── scripts/                    ← 数据采集/特征计算/训练脚本
├── sql/                        ← 数据库建表与分析 SQL
├── data/
│   ├── luckin_spatial_backup.sql  ← 完整数据库备份（建表 + 全部门店/POI 数据，34MB）
│   ├── models/xgboost_location_model.pkl  ← 训练好的选址预测模型
│   └── raw/                    ← 高德采集的原始门店/POI 数据
├── .env                        ← 环境配置（高德 Key、数据库连接）
└── deploy/                     ← 离线部署资源（本包新增）
    ├── wheels/                 ← 后端 Python 离线依赖（35 个 wheel，适配 Python 3.11 x64）
    ├── requirements-runtime.txt ← 后端运行时依赖清单（精确锁定版本）
    ├── node/                   ← 便携版 Node.js v24.12.0（无需系统安装）
    └── postgis/                ← PostGIS 3.6.2 捆绑包（postgis-bundle-pg15-3.6.2x64.zip）
```

---

## 二、部署前唯一需要自备的两样东西

离线包已解决**应用层面的所有依赖**，但下面两样**基础运行环境**仍需在目标电脑上安装一次：

### 1. Python 3.11（务必 3.11.x）

> 离线 wheel 是专为 Python 3.11 x64 打包的，其他版本（3.12/3.13）无法离线安装。

- 下载地址：https://www.python.org/downloads/release/python-3119/
- 选择 `Windows installer (64-bit)`
- ⚠️ 安装时**务必勾选 "Add python.exe to PATH"**

验证：
```cmd
python --version
:: 输出应为 Python 3.11.x
```

### 2. PostgreSQL 15（务必 15.x）

- 下载地址：https://get.enterprisedb.com/postgresql/postgresql-15.8-1-windows-x64.exe
- 安装时：
  - **超级用户密码设为 `your_db_password_here`**（本包脚本依赖此密码）
  - 端口保持默认 `5432`
  - 安装目录保持默认 `C:\Program Files\PostgreSQL\15`
- 安装完成后，PostGIS 扩展需要额外安装（见下一步「第三步」）

---

## 三、部署步骤（三步搞定）

### 第一步：双击 `安装部署.bat`

脚本会自动完成：

1. **检查 Python**（版本不符会提示）
2. **离线安装后端依赖**：`pip install --no-index --find-links deploy\wheels`（无需联网）
3. **检查并启动 PostgreSQL 服务**
4. **初始化数据库**：
   - 创建数据库用户 `luckin`（超级用户，密码 `your_db_password_here`）
   - 创建数据库 `luckin_spatial`
   - 从 `data/luckin_spatial_backup.sql` **恢复全部门店、POI、预计算数据**（约 1-3 分钟）

> 若数据库已有数据，脚本会自动跳过恢复步骤，可安全重复执行。

### 第二步：安装 PostGIS 3.6 扩展（只需一次）

PostgreSQL 装完后默认**不含** PostGIS 空间扩展，需要手动装一次（本包已附带捆绑包）：

```powershell
# 1. 解压 deploy\postgis\postgis-bundle-pg15-3.6.2x64.zip
# 2. 停止 PostgreSQL 服务
net stop postgresql-x64-15

# 3. 将解压出的文件复制到 PostgreSQL 15 目录：
#    bin\*.dll          → C:\Program Files\PostgreSQL\15\bin\
#    lib\*              → C:\Program Files\PostgreSQL\15\lib\
#    share\extension\*  → C:\Program Files\PostgreSQL\15\share\extension\
#    share\contrib\*    → C:\Program Files\PostgreSQL\15\share\contrib\
#    gdal-data\*        → C:\Program Files\PostgreSQL\15\gdal-data\

# 4. 重启 PostgreSQL 服务
net start postgresql-x64-15
```

> 说明：数据库备份文件里已包含 `CREATE EXTENSION postgis` 等语句，
> 安装 PostGIS 后运行 `安装部署.bat` 恢复数据即可自动启用空间扩展。

### 第三步：双击 `启动服务.bat`

一键启动全部服务，启动完成后：

| 服务 | 地址 |
|------|------|
| 前端页面 | http://localhost:3000 |
| 后端 API 文档 | http://localhost:8000/docs |
| 后端健康检查 | http://localhost:8000/health （返回 `{"status":"ok"}`） |
| 数据库 | localhost:5432 |

---

## 四、验证是否部署成功

按顺序检查：

1. 浏览器打开 http://localhost:3000 → 能看到首页仪表盘和全国门店地图
2. 浏览器打开 http://localhost:8000/health → 显示 `{"status":"ok"}`
3. 切换到「POI 分析」「竞品对比」「选址预测」页面 → 能正常出图表（说明数据已恢复）

---

## 五、常用命令

```cmd
:: 启动 / 停止 / 重启 / 状态
python manage.py start
python manage.py stop
python manage.py restart
python manage.py status

:: 仅启动后端 / 仅启动前端
python manage.py backend
python manage.py frontend

:: 查看日志
python manage.py logs backend
python manage.py logs frontend
```

---

## 六、常见问题排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `安装部署.bat` 提示找不到 PostgreSQL | 未安装 PG15，或服务名不同 | 安装 PostgreSQL 15.8（密码 `your_db_password_here`） |
| 离线安装依赖报错找不到匹配版本 | Python 不是 3.11 | 卸载后重装 Python 3.11 x64 |
| 恢复数据时 PostGIS 报错 | PostGIS 未安装 | 按「第三步」安装 PostGIS 捆绑包 |
| 前端能开但地图空白 | 底图瓦片需联网加载 | 高德底图瓦片需要外网；分析数据不依赖联网 |
| 端口 8000/3000 被占用 | 旧进程残留 | 运行 `python manage.py stop` 后重试 |
| 预测接口返回兜底评分 | 模型未加载 | 确认 `data/models/xgboost_location_model.pkl` 存在 |

---

## 七、技术说明（本包做了什么增强）

1. **补齐了 `requirements-api.txt` 的两个遗漏依赖**：
   - `aiohttp`（`backend/routers/geo.py` 调用高德逆地理编码用）
   - `xgboost`（`joblib.load` 加载 `XGBClassifier` 模型时隐式依赖）
2. **依赖版本精确锁定**为本机已验证可用的版本（fastapi 0.139.2 / xgboost 3.2.0 等），
   确保模型加载与后端运行 100% 复现当前环境。
3. **便携版 Node.js** 内置在 `deploy/node/`，`启动服务.bat` 和 `manage.py` 会自动优先使用，
   目标电脑即使没装 Node 也能运行前端。
