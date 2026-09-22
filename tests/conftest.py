"""全局测试夹具：把测试与「机主的个人配置」彻底隔开。

## 为什么要隔开

`get_settings()` 每次都 `load_dotenv(PROJECT_DIR/".env")` 并读 `PROJECT_DIR/config.yaml`。
这两个文件都被 gitignore，是**机主本机的**东西，于是测试实际跑在一份谁也没声明过的配置上：

- `.env` 里的 `JOB_ONE_STOP_CONTEXT_REPO_PATH` 指向真实个人仓库。用例即使 `delenv` 也拦不住
  （删掉的变量马上被 load_dotenv 填回），后果不是「断言挂了」这么轻：`ContextWriter` 拿到真
  路径后，真的把测试候选写进了本人看板的收集箱列（实测污染 9 行）。
- `.env` 里的 `AI_PROVIDER_KEY_*` 配合 config.yaml 的 `ai.providers`，让「忘了桩掉模型调用」
  的用例真的连上模型服务商（违反 CLAUDE.md §4「测试不得联网」，还烧真钱）。
- 反方向也一样糟：**23 个用例其实是靠 config.yaml 里的 `ai.enabled: true` 才通过的**。
  在没有个人 config.yaml 的干净检出里，`chat_ingest.py` 的
  `ai_enabled = bool(ai_cfg.get("enabled")) and is_ai_available()` 左半边为假 → ingest 整条
  抽取链路短路 → 被桩掉的 `extract_jobs_freeform` 根本不会被调用 → 没有候选 → 400。
  这类失败与代码质量无关，纯粹是「测试没有自带配置」。

所以基线由测试自己声明：配置来自已入库的 `config.example.yaml`（经
`scripts/lib/testing_config.py` 中和），密钥是假值，数据目录在 tmp_path。
判据是「没有个人 `.env`、没有个人 `config.yaml` 的干净检出中，全部用例都能跑」。

## 夹具清单（全部 autouse）

1. `isolate_local_settings`：掐掉 load_dotenv，铺好基线配置、假密钥与 tmp 数据目录。
2. `no_outbound_network`：非回环目标的名字解析与连接一律抛错。
3. `no_unstubbed_model_calls`：在 `ai._chat` 上抛错，未桩掉的模型调用快速失败而不是慢慢重试。
4. `block_context_repo_writes`：兜住写入真实个人仓库这一条红线。
5. `decision_llm_calls`：桩掉决策分析的模型调用，默认走规则降级。

绊线在 `tests/test_isolation.py`：上面每条都有对应用例，破坏中和会当场翻红。
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]

# conftest 导入时的环境快照，用来区分「变量来自调用者的 shell」和「变量由 load_dotenv
# 从机主 .env 灌进来」。此刻还没有任何代码调用过 get_settings()（load_dotenv 只在它内部
# 调用），所以这份快照就是纯净的父进程环境。`tests/test_isolation.py` 拿它做对照——
# 少了这个基准就分不开两者：开发机的 shell 往往导出着和 .env 同名同值的变量（如代理），
# 直接比对会得到假阳性。
PRISTINE_ENVIRON = dict(os.environ)

sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))

import testing_config  # noqa: E402  仓库内的测试支撑模块，非第三方包


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "exercises_ai_chat: 被测对象就是 ai._chat 本身，豁免 no_unstubbed_model_calls 的拦截",
    )


def _real_context_repo_root() -> Path | None:
    """在隔离生效前探出机主真实上下文仓库的路径，供 `block_context_repo_writes` 设防。

    刻意同时看进程环境**和** `.env` 文件：隔离之后 `os.environ` 里那份已被置空，
    而只写在 `.env` 里、从未导出到 shell 的那份才是最常见的形态。
    """
    from dotenv import dotenv_values

    raw = (os.environ.get("JOB_ONE_STOP_CONTEXT_REPO_PATH") or "").strip()
    if not raw:
        env_file = _REPO_ROOT / ".env"
        if env_file.exists():
            raw = (dotenv_values(env_file).get("JOB_ONE_STOP_CONTEXT_REPO_PATH") or "").strip()
    if not raw:
        return None

    # `_to_wsl_path` 在 `backend.app.config` 里，不在 context_repository。此前这里写的是
    # `hasattr(context_repository, "_to_wsl_path")`，恒为 False，于是盘符路径从不转换：
    # `.env` 写成 `D:\...` 时，防线比对的是未转换的字符串，而 ContextRepository 用的是
    # `/mnt/d/...`，两者永不相等 → 防线静默失效。本机 `.env` 是 POSIX 写法所以没暴露。
    from backend.app.config import _to_wsl_path

    try:
        return Path(_to_wsl_path(raw)).expanduser().resolve()
    except (OSError, ValueError):
        return None


# 进程启动时算一次：此后各用例的 monkeypatch 都改不掉它，防线不会被自己的隔离绕过。
_REAL_CONTEXT_REPO_ROOT = _real_context_repo_root()


@pytest.fixture(autouse=True)
def isolate_local_settings(monkeypatch, tmp_path):
    """铺好每个用例的配置基线：不读机主 `.env`，配置来自中和后的 `config.example.yaml`。

    两件事必须同时做，少一件都不成立：

    1. **掐掉 `load_dotenv`**。只 `delenv` 是没用的——`get_settings()` 每次都重新
       `load_dotenv(PROJECT_DIR/".env")`，被删掉的变量立刻被填回，于是「没配 key」的用例
       反而拿到真 key。换成 no-op 之后，`delenv` 才真的删得掉，用例也不必再靠「置空而不
       delenv」这种绕法表达「未配置」（原有写法照旧有效，不用改）。
    2. **给出显式基线**。掐掉 `.env` 只解决了「不该有的值」，还要补上「该有的值」：
       `ai.enabled` 这类开关来自 config.yaml，缺了它 23 个 ingest 用例会挂在 400
       （详见模块 docstring）。基线走 `scripts/lib/testing_config.py`，与三条冒烟脚本共用
       同一份中和清单，避免两边各记一遍、各漏一处。

    用例自己 `monkeypatch.setenv` 的值优先级更高（在本夹具之后生效），既有用例无需改动。
    """
    from backend.app import config

    # load_dotenv 在 config 模块里是 from-import 进来的名字，要打在那个命名空间上。
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)

    data_dir = tmp_path / "baseline-data"
    config_path = tmp_path / "baseline-config.yaml"
    testing_config.write(config_path, data_dir=data_dir)
    monkeypatch.setenv("JOB_ONE_STOP_CONFIG", str(config_path))
    monkeypatch.setenv("JOB_ONE_STOP_DATABASE_URL", f"sqlite:///{tmp_path / 'baseline.sqlite3'}")

    # 假密钥：形态与真值一致（读取侧只判空/非空），但连不上任何真实服务。
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-baseline")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ai.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-baseline")
    # 上下文仓库：置空 = 未配置。要用的用例自己 setenv 到 tmp_path。
    monkeypatch.setenv("JOB_ONE_STOP_CONTEXT_REPO_PATH", "")

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


_LOOPBACK_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "testserver"}


def _is_loopback_host(host: object) -> bool:
    if not isinstance(host, str) or not host:
        return False
    if host.lower() in _LOOPBACK_HOSTNAMES:
        return True
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch):
    """任何指向非回环地址的名字解析或连接都当场抛错。

    比逐个桩掉抓取函数更早一层：桩漏了一个（新来源、新 AI 调用点）就会真的出网，而症状
    往往只是「用例慢到离谱」或偶发挂住，很难归因到"漏了个桩"。

    **拦在 `getaddrinfo` 而不只是 `connect`**：域名目标在解析阶段就失败了，压根走不到
    connect——只拦 connect 的话，一个连不通的域名表现为「慢」而不是「错」，正是最难查的
    那种。字面 IP 不经解析，所以 connect 那层也要留着。
    回环一律放行：`httpx.ASGITransport` 不走 socket，但本地起服务的用例需要它。
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        if not _is_loopback_host(host):
            raise AssertionError(
                f"测试试图解析外部主机 {host!r}:{port}（CLAUDE.md §4：测试不得联网）。"
                "请 monkeypatch 掉对应的抓取/模型调用，或把目标指向 127.0.0.1。"
            )
        return real_getaddrinfo(host, port, *args, **kwargs)

    def _check_connect(address):
        host = address[0] if isinstance(address, tuple) and address else None
        if not isinstance(host, str):
            return  # AF_UNIX 等非 IP 地址：与出网无关，放行。
        if not _is_loopback_host(host):
            raise AssertionError(
                f"测试试图连接非回环地址 {host!r}（CLAUDE.md §4：测试不得联网）。"
                "请 monkeypatch 掉对应的抓取/模型调用。"
            )

    def guarded_connect(self, address):
        _check_connect(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        _check_connect(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture(autouse=True)
def no_unstubbed_model_calls(monkeypatch, request):
    """未桩掉的模型调用一律立刻失败，而不是去连一个不存在的 base_url。

    基线刻意保留 `ai.enabled: true` + 假 key（`is_ai_available()` 要为真，否则 ingest
    抽取链路整体短路，见 `isolate_local_settings`）。代价是每个没桩到位的调用点都会真的
    发请求：实测 sprint brief 那两个用例经 `prep_ops.tailor_interview_prep_llm` 去连
    `ai.invalid`，按 provider 退避重试，单独吃掉 58 秒。

    在 `ai._chat`（所有模型调用的唯一出口）上抛错，等价于「provider 全部失败」——那是
    产品里本来就存在的降级分支，调用方的 try/except 会走既有的规则/模板回退，被测行为不变，
    只是快了两个数量级。要真正验证模型路径的用例自己 `monkeypatch.setattr` 覆盖它，或桩掉
    更上层的 `extract_jobs_freeform` / `tailor_interview_prep_llm`（现有用例都是这么做的）。

    以 `@pytest.mark.exercises_ai_chat` 标记的用例豁免——`_chat` 本身就是它们的被测对象
    （`tests/test_ai_client.py` 全模块如此）。它们自己把 `openai.OpenAI` 换成桩，且
    `no_outbound_network` 照旧生效，所以豁免的只是这层拦截，不是联网防线。
    """
    if request.node.get_closest_marker("exercises_ai_chat"):
        return

    from backend.app.services import ai

    def blocked(*_args, **_kwargs):
        raise RuntimeError(
            "测试里出现未桩掉的模型调用（ai._chat）。请桩掉对应的上层函数"
            "（如 extract_jobs_freeform / tailor_interview_prep_llm），"
            "或给用例加 @pytest.mark.exercises_ai_chat 以直接验证 _chat 本身。"
        )

    monkeypatch.setattr(ai, "_chat", blocked)


@pytest.fixture(autouse=True)
def block_context_repo_writes(monkeypatch):
    """兜底：禁止测试把任何字节写进**真实**个人上下文仓库。

    `isolate_local_settings` 已经把该变量置空、也不再读 `.env`，正常路径下这条防线不会
    触发。留着是因为它守的那次事故代价太高（实测把测试候选写进了本人看板收集箱列 9 行），
    而成本只是一次路径比对：从 shell 导出过该变量、或将来某个用例自己指回真路径时，
    这里仍会当场拦住。写入目标落在真实仓库内就抛错，用例写 tmp_path 不受影响。
    """
    if _REAL_CONTEXT_REPO_ROOT is None:
        return

    from backend.app.services import context_repository

    guarded = _REAL_CONTEXT_REPO_ROOT
    original = context_repository.ContextWriter.insert_line_in_section

    def guarded_insert(self, key: str, section_heading: str, line: str):
        if self.root is not None and (self.root == guarded or guarded in self.root.parents or self.root in guarded.parents):
            raise AssertionError(
                f"测试试图写入真实个人上下文仓库（{key} @ {self.root}）。"
                "用例必须把 JOB_ONE_STOP_CONTEXT_REPO_PATH 指向 tmp_path。"
            )
        return original(self, key, section_heading, line)

    monkeypatch.setattr(context_repository.ContextWriter, "insert_line_in_section", guarded_insert)


@pytest.fixture(autouse=True)
def decision_llm_calls(monkeypatch):
    """桩掉建议/问答里的模型调用，返回记录调用参数的列表（默认零联网、走规则降级）。

    这两处（候选建议 `services/advice.py`、问答落盘 `services/decision_reply.py`）都挂在
    主流程后面，漏桩就会真的发请求。默认返回 None = 「模型不可用，按规则引擎的确定性结论
    走」，是产品里本来就存在的降级路径，不改变任何被测行为。要断言「模型确实被调用/被跳过」
    的用例，用本夹具看调用记录，或自己 `monkeypatch.setattr` 覆盖掉对应模块里的这个桩。
    """
    from backend.app.services import advice, decision_reply

    calls: list[dict] = []

    def fake_analyze(**kwargs):
        calls.append(kwargs)
        return None

    for module in (advice, decision_reply):
        monkeypatch.setattr(module, "analyze_decision_chat_llm", fake_analyze)
    return calls
