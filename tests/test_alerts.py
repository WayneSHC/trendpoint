# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
AlertManager 的管道判定與 send_alert 回傳值語意測試。全離線。

回傳值語意是「是否確實送達外部管道」，不是「是否處理完畢」。這個區別有實際
後果：monitor_signals 以該值決定要不要寫入 sent_alerts 去重表，而該表意謂
「已通知使用者」。Mock 若回傳 True，未送出的警報會被記成已送出，等到憑證
補齊時該筆訊號再也不會發出。
"""

import pytest

from alerts import AlertManager

CHANNEL_VARS = ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_TO",
                "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """回傳一個工廠：環境變數完全由參數決定，不讀取真實 .env 或外部環境。"""
    def _make(**env):
        for var in CHANNEL_VARS:
            monkeypatch.delenv(var, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.chdir(tmp_path)  # alerts.log 寫在暫存目錄，不污染工作區
        return AlertManager(env_filepath=str(tmp_path / "no-such.env"))
    return _make


def test_mock_send_reports_not_delivered(manager, capsys):
    """
    無憑證 → Mock 模式 → send_alert 必須回傳 False。

    這是本檔的核心斷言：呼叫端據此不寫去重表，警報下輪會再度出現，
    直到真的送達為止。
    """
    mgr = manager()
    assert mgr.is_mock

    assert mgr.send_alert("測試訊息") is False, \
        "Mock 未送達任何管道，回傳 True 會讓呼叫端把它記成已送出"
    assert "[MOCK 推播警報]" in capsys.readouterr().out


def test_mock_still_writes_local_log(manager, tmp_path):
    """回傳 False 只代表未送達外部管道；本地留痕仍須發生，否則等於整筆遺失。"""
    mgr = manager()
    mgr.send_alert("<b>粗體</b>訊息")

    log = tmp_path / "alerts.log"
    assert log.exists(), "Mock 模式仍應寫入本地通知日誌"
    content = log.read_text(encoding="utf-8")
    assert "粗體訊息" in content, "純文字輸出應已剝除 HTML 標籤"


@pytest.mark.parametrize("env,expect_mock,expect_line,expect_tg", [
    ({}, True, False, False),
    ({"LINE_CHANNEL_ACCESS_TOKEN": "t"}, True, False, False),   # 缺 LINE_TO
    ({"LINE_TO": "u"}, True, False, False),                     # 缺 token
    ({"LINE_CHANNEL_ACCESS_TOKEN": "t", "LINE_TO": "u"}, False, True, False),
    ({"TELEGRAM_TOKEN": "t"}, True, False, False),              # 缺 chat id
    ({"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}, False, False, True),
])
def test_channel_enablement_requires_complete_pair(
        manager, env, expect_mock, expect_line, expect_tg):
    """
    管道須「成對」配置才算啟用——半套配置等同未配置。

    今日實測的故障正是半套：repo 只設了 LINE_TO、沒有 LINE_CHANNEL_ACCESS_TOKEN，
    於是靜默退回 Mock。這組參數化把「差一個變數」的每種情況都釘住。
    """
    mgr = manager(**env)

    assert mgr.is_mock is expect_mock
    assert mgr.line_enabled is expect_line
    assert mgr.tg_enabled is expect_tg
