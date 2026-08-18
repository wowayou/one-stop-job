# Docker 构建排障

本项目日常试用和开发优先走 [QUICKSTART.md](../QUICKSTART.md) 的本地开发模式。Docker Compose 保留给 Windows 一键运行、交接部署和需要前后端单端口托管的场景。

## 当前构建方式

- 前端阶段使用 `frontend/package-lock.json` 和 `npm ci`。
- 后端阶段用 `requirements-runtime.txt` 单层安装（`--timeout 300` 兜底大包），BuildKit cache mount 负责缓存。
- 默认 npm registry 是 `https://registry.npmmirror.com`。
- 默认 PyPI index 是 `https://mirrors.aliyun.com/pypi/simple`。
- BuildKit cache 会缓存 npm 和 pip 下载结果。

常规构建：

```bash
docker compose up -d --build
```

指定镜像源：

```bash
NPM_REGISTRY=https://registry.npmmirror.com \
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
docker compose up -d --build
```

## 常见错误

### `Read timed out` / `i/o timeout`

先换单一 PyPI 镜像源，不要同时配置多个很慢的源：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple docker compose up -d --build
```

仍失败时切回官方源验证是否是镜像同步问题：

```bash
PIP_INDEX_URL=https://pypi.org/simple docker compose up -d --build
```

### `ECONNRESET` / npm 下载失败

切换 npm registry：

```bash
NPM_REGISTRY=https://registry.npmjs.org docker compose up -d --build
```

### `No matching distribution found`

通常是镜像源索引未同步。切换 PyPI index 后重试：

```bash
PIP_INDEX_URL=https://pypi.org/simple docker compose up -d --build
```

### `database is locked`

不要同时运行本地开发和 Docker。默认情况下：

- 本地开发使用 `config.yaml general.data_dir` 下的 SQLite 数据。
- Docker 使用 volume `job_one_stop_data`。

### Windows WSL 路径翻译失败

如果双击 bat 时看到：

```text
wsl: Failed to translate 'E:\Dev\...'
```

通常是 Windows PATH 中有 WSL 无法翻译的失效路径。它一般不影响容器启动；要消除提示，清理 Windows 环境变量里的无效 PATH 项。

## 诊断命令

```bash
scripts/docker_doctor.sh
scripts/deploy_check.sh
docker compose build --progress=plain
docker compose logs -f app
```

`scripts/docker_doctor.sh --pull-check` 会真实拉取基础镜像，需要网络可用。

## 构建后验证

```bash
docker compose ps
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/ready
```

停止：

```bash
docker compose down
```
