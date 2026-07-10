# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
TrendPoint - 通知推播核心模組 (Alert Manager)

本模組支援：
1. 載入 .env 檔案中的 LINE Messaging API 與 Telegram Bot Token。
2. 發送即時通知至 LINE（Messaging API 推播）或 Telegram。
3. 當無 Token 設定時，自動降級為本地 Mock 模式（美化終端機輸出與寫入 alerts.log 檔案）。

註：舊版 LINE Notify 服務已於 2025-03-31 由 LINE 官方終止，
原 https://notify-api.line.me/api/notify 端點已停止服務，
故改用 LINE Messaging API 的 push message 端點推播。
需設定 LINE_CHANNEL_ACCESS_TOKEN（頻道存取權杖）與
LINE_TO（推播目標的 userId / groupId）兩項環境變數。
"""

import os
import requests
import datetime
from typing import Dict, Optional

def load_dotenv(filepath: str = ".env") -> Dict[str, str]:
    """
    輕量化自定義 .env 讀取器，避免引入額外的 python-dotenv 套件。
    """
    env_vars = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # 排除註解與空行
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    # 去除多餘空格與雙引號
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    env_vars[key] = val
        except Exception as e:
            print(f"警告：讀取 .env 檔案時發生錯誤: {e}")
    return env_vars

class AlertManager:
    """
    通知推播管理器，整合 LINE Messaging API 與 Telegram Bot API。
    """
    def __init__(self, env_filepath: str = ".env"):
        # 優先載入系統環境變數，再載入 .env 檔案
        self.config = load_dotenv(env_filepath)

        def _get(key: str) -> Optional[str]:
            return os.environ.get(key) or self.config.get(key)

        # LINE Messaging API：需頻道存取權杖與推播目標 ID
        self.line_token = _get("LINE_CHANNEL_ACCESS_TOKEN")
        self.line_to = _get("LINE_TO")
        self.tg_token = _get("TELEGRAM_TOKEN")
        self.tg_chat_id = _get("TELEGRAM_CHAT_ID")

        # 兩個推播管道皆須完整配置才視為啟用
        self.line_enabled = bool(self.line_token and self.line_to)
        self.tg_enabled = bool(self.tg_token and self.tg_chat_id)

        # 判定是否處於 Mock 模式
        self.is_mock = not (self.line_enabled or self.tg_enabled)
        
        # 記錄檔路徑
        self.log_filepath = "alerts.log"

    def send_line_message(self, message: str) -> bool:
        """
        透過 LINE Messaging API 的 push message 端點發送通知。
        """
        if not self.line_enabled:
            return False

        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Authorization": f"Bearer {self.line_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": self.line_to,
            "messages": [{"type": "text", "text": message}],
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                print("LINE 推播發送成功。")
                return True
            else:
                print(f"LINE 推播發送失敗。狀態碼: {response.status_code}，原因: {response.text}")
                return False
        except Exception as e:
            print(f"LINE 推播網路連線錯誤: {e}")
            return False

    def send_telegram(self, message: str) -> bool:
        """
        發送通知至 Telegram Bot。
        """
        if not self.tg_token or not self.tg_chat_id:
            return False
            
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {
            "chat_id": self.tg_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("Telegram 通知發送成功。")
                return True
            else:
                print(f"Telegram 發送失敗。狀態碼: {response.status_code}，原因: {response.text}")
                return False
        except Exception as e:
            print(f"Telegram 網路連線錯誤: {e}")
            return False

    def send_alert(self, message: str, raw_text: Optional[str] = None) -> bool:
        """
        全域推播接口，根據環境變數配置決定發送管道。
        若未配置則進入 Mock 模式。
        """
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 處理 HTML 標籤以便純文字輸出
        plain_message = raw_text if raw_text else message
        if "<b>" in plain_message or "</b>" in plain_message:
            plain_message = plain_message.replace("<b>", "").replace("</b>", "")
            
        # 1. 寫入本地日誌檔案
        try:
            with open(self.log_filepath, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {plain_message}\n")
        except Exception as e:
            print(f"寫入本地通知日誌失敗: {e}")

        # 2. 真實推播發送
        sent_ok = False
        if not self.is_mock:
            if self.line_enabled:
                # LINE Messaging API 文字訊息不解析 HTML，發送純文字
                sent_ok = self.send_line_message(plain_message) or sent_ok
            if self.tg_enabled:
                # Telegram 支援 HTML 標記
                sent_ok = self.send_telegram(message) or sent_ok
            return sent_ok
        else:
            # 3. Mock 模式輸出 (美化終端機呈現)
            print("\n" + "#" * 60)
            print(f"📢 [MOCK 推播警報] - {timestamp}")
            print("-" * 60)
            print(plain_message)
            print("#" * 60 + "\n")
            return True
