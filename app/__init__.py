"""AI 趋势雷达 —— 后端应用包。

在包导入时加载项目根目录的 .env，供本地开发使用；
云托管平台请直接使用平台提供的环境变量（不会读取本文件中的密钥）。
"""

from pathlib import Path

from dotenv import load_dotenv

# 项目根目录下的 .env（本地开发用）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")