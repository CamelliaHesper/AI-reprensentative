from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', env_file_encoding='utf-8')
    data_dir: Path = ROOT / 'data'
    feishu_app_id: str = ''
    feishu_app_secret: str = ''
    feishu_bot_open_id: str = ''
    feishu_reply: bool = True
    model_api_key: str = ''
    model_base_url: str = ''
    model_name: str = ''
    model_proxy_url: str = ''
    vision_api_key: str = ''
    vision_base_url: str = ''
    vision_model: str = ''
    vision_proxy_url: str = ''
    max_pdf_pages: int = Field(default=30,ge=1,le=100)
    max_file_mb: int = Field(default=25, ge=1, le=100)
    max_zip_mb: int = Field(default=100, ge=1, le=500)
    max_zip_entries: int = Field(default=100, ge=1, le=1000)
    max_text_chars: int = Field(default=100000, ge=100, le=1000000)
    libreoffice_path: str = ''
    web_url: str = 'http://127.0.0.1:8000'
    onlyoffice_url: str = 'http://127.0.0.1:8082'
    onlyoffice_bridge_url: str = 'http://host.docker.internal:8001'

    @property
    def model_ready(self):
        return bool(self.model_api_key and self.model_name and self.model_base_url)
