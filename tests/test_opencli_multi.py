from backend.app.services.collectors import OpenCLIMultiCommandCollector, command_with_query


def test_command_with_query_replaces_token_after_search():
    cmd = ["opencli.cmd", "boss", "search", "旧词", "--city", "青岛"]
    assert command_with_query(cmd, "新词") == ["opencli.cmd", "boss", "search", "新词", "--city", "青岛"]
    assert command_with_query(cmd, "新词") is not cmd  # 不改原列表
    no_search = ["opencli.cmd", "boss", "joblist"]
    assert command_with_query(no_search, "新词") == no_search


def test_multi_command_collector_dedupes_and_reports(monkeypatch):
    calls: list[str] = []

    def fake_collect(self):
        calls.append(self.command[3])
        if self.command[3] == "坏词":
            raise RuntimeError("boom")
        return [
            {"external_id": "a", "title": f"{self.command[3]}-岗1"},
            {"external_id": "b", "title": "共享岗"},
        ]

    monkeypatch.setattr("backend.app.services.collectors.OpenCLICommandCollector.collect", fake_collect)
    collector = OpenCLIMultiCommandCollector(
        opencli_path="",
        commands=[
            ["x", "boss", "search", "词一"],
            ["x", "boss", "search", "坏词"],
            ["x", "boss", "search", "词二"],
        ],
        rate_limit_seconds=0,
    )
    records = collector.collect()

    assert calls == ["词一", "坏词", "词二"]
    # external_id 跨命令去重：a/b 只保留首个来源的记录，词二只贡献重复项被丢弃。
    assert [r["external_id"] for r in records] == ["a", "b"]
    assert collector.report["commands_ok"] == 2
    assert collector.report["jobs"] == 2
    assert collector.report["skipped"] == [{"command": "boss search 坏词", "reason": "boom"}]


def test_multi_command_collector_raises_when_all_fail(monkeypatch):
    def fake_collect(self):
        raise RuntimeError("全挂")

    monkeypatch.setattr("backend.app.services.collectors.OpenCLICommandCollector.collect", fake_collect)
    collector = OpenCLIMultiCommandCollector(
        opencli_path="", commands=[["x", "boss", "search", "词一"]], rate_limit_seconds=0
    )
    try:
        collector.collect()
        raise AssertionError("应当抛错")
    except RuntimeError as exc:
        assert "全部关键词采集失败" in str(exc)
