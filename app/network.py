"""Explicit per-service networking, independent of Windows/VPN proxy settings."""
import asyncio
import os
import threading
import httpx


def configure_feishu_process():
    # Only called by the dedicated receiver entry point. No Windows/system changes.
    # requests in the SDK's endpoint discovery also consults Windows proxy settings.
    os.environ['NO_PROXY']='*'
    os.environ['no_proxy']='*'


def model_http_options(cfg,vision=False):
    proxy=cfg.vision_proxy_url if vision else cfg.model_proxy_url
    return {'trust_env':False,'proxy':proxy.strip() or None}


def invoke_chat(cfg,messages,vision=False,timeout=90):
    from langchain_openai import ChatOpenAI
    options=model_http_options(cfg,vision)
    async_client=httpx.AsyncClient(**options,timeout=timeout)
    try:
        with httpx.Client(**options,timeout=timeout) as client:
            llm=ChatOpenAI(api_key=cfg.vision_api_key if vision else cfg.model_api_key,
                base_url=cfg.vision_base_url if vision else cfg.model_base_url,
                model=cfg.vision_model if vision else cfg.model_name,
                temperature=0,timeout=timeout,max_retries=1,openai_proxy='',http_client=client,http_async_client=async_client)
            return llm.invoke(messages)
    finally:
        # The async client is supplied explicitly so SDK initialization cannot inherit proxies.
        def close():asyncio.run(async_client.aclose())
        try:asyncio.get_running_loop()
        except RuntimeError:close()
        else:
            thread=threading.Thread(target=close);thread.start();thread.join()
