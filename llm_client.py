import requests
import json
import time
import re
import logging
from config_loader import load_config

logger = logging.getLogger(__name__)

# 全局活跃配置缓存 (api_key, model_name)
_CACHED_ACTIVE_CONFIG = None
_CACHE_EXPIRES_AT = 0  # Unix timestamp，过期后重新探测
_CACHE_TTL = 300  # 缓存有效期 5 分钟

# #13 API Key 健康追踪
# key_prefix -> {"failures": int, "cooldown_until": float, "disabled": bool}
_KEY_HEALTH = {}


def _key_prefix(api_key):
    """取 key 前 8 位作为标识"""
    return api_key[:8] if api_key else "unknown"


def _is_key_healthy(api_key):
    """检查 key 是否可用（未被禁用且不在冷却中）"""
    prefix = _key_prefix(api_key)
    health = _KEY_HEALTH.get(prefix)
    if not health:
        return True
    if health.get("disabled"):
        return False
    if health.get("cooldown_until", 0) > time.time():
        return False
    return True


def _record_key_failure(api_key, status_code):
    """记录 key 失败，按类型分级处理"""
    prefix = _key_prefix(api_key)
    if prefix not in _KEY_HEALTH:
        _KEY_HEALTH[prefix] = {"failures": 0, "cooldown_until": 0, "disabled": False}

    h = _KEY_HEALTH[prefix]

    if status_code in (401, 403):
        # 认证失败：永久禁用
        h["disabled"] = True
        logger.warning(f"  🔒 Key {prefix}... 认证失败({status_code})，永久禁用")
    elif status_code == 429:
        # 限流：60s 冷却
        h["cooldown_until"] = time.time() + 60
        logger.warning(f"  ⏳ Key {prefix}... 限流(429)，冷却 60s")
    elif status_code in (500, 502, 503):
        # 服务器错误：递增退避
        h["failures"] += 1
        if h["failures"] >= 5:
            cooldown = 300  # 5 分钟
        elif h["failures"] >= 3:
            cooldown = 60
        else:
            cooldown = 10
        h["cooldown_until"] = time.time() + cooldown
        logger.warning(f"  ⏳ Key {prefix}... 服务器错误({status_code})，连续 {h['failures']} 次，冷却 {cooldown}s")


def _record_key_success(api_key):
    """记录成功，重置失败计数"""
    prefix = _key_prefix(api_key)
    if prefix in _KEY_HEALTH:
        _KEY_HEALTH[prefix]["failures"] = 0
        _KEY_HEALTH[prefix]["cooldown_until"] = 0

def get_config():
    return load_config().api

def _check_config_health(api_key, model_name, base_url):
    """私有探测：用极小成本检查 Key/模型 是否可用"""
    if not api_key or not model_name: return False
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5
        }
        r = requests.post(base_url, headers=headers, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def get_active_config():
    """
    核心优先级逻辑（带 TTL 缓存）：
    1. Cheap Key + Gemini (便宜G)
    2. Premium Key + Gemini (贵G)
    3. Cheap Key + GPT (便宜GPT)
    4. Premium Key + GPT (贵GPT)

    缓存 5 分钟内不重复探测，避免浪费 API 额度。
    """
    global _CACHED_ACTIVE_CONFIG, _CACHE_EXPIRES_AT
    if _CACHED_ACTIVE_CONFIG and time.time() < _CACHE_EXPIRES_AT:
        return _CACHED_ACTIVE_CONFIG

    c = get_config()
    base_url = c.get('base_url')
    
    # 定义 4 级优先级队列 - 字段对齐 config_loader.py
    priority_queue = [
        {"name": "Level 1 (Cheap Key + Gemini)", "key": c.get('api_key_cheap'), "model": c.get('model')},
        {"name": "Level 2 (Premium Key + Gemini)", "key": c.get('api_key_premium'), "model": c.get('model')},
        {"name": "Level 3 (Cheap Key + GPT)", "key": c.get('api_key_cheap'), "model": c.get('model_backup')},
        {"name": "Level 4 (Premium Key + GPT)", "key": c.get('api_key_premium'), "model": c.get('model_backup')}
    ]

    logger.info(f"📡 正在进行 AI 优先级通路预检（共 {len(priority_queue)} 级，最坏 {len(priority_queue)*10}s）...")

    for node in priority_queue:
        if node['key'] and node['model']:
            if _check_config_health(node['key'], node['model'], base_url):
                logger.info(f"✅ 已锁定通路: {node['name']}")
                _CACHED_ACTIVE_CONFIG = (node['key'], node['model'])
                _CACHE_EXPIRES_AT = time.time() + _CACHE_TTL
                return _CACHED_ACTIVE_CONFIG
            else:
                logger.warning(f"❌ 通路受限: {node['name']}")

    # 终极兜底
    logger.error("🚨 所有预设 AI 通路均不可用，请检查 API 余额或网络！")
    fallback_key, fallback_model = c.get('api_key_cheap'), c.get('model')
    if fallback_key and fallback_model:
        _CACHED_ACTIVE_CONFIG = (fallback_key, fallback_model)
        _CACHE_EXPIRES_AT = time.time() + 60  # 兜底只缓存 1 分钟
    else:
        _CACHED_ACTIVE_CONFIG = None
        _CACHE_EXPIRES_AT = 0
    return _CACHED_ACTIVE_CONFIG

def call_ai_api(messages, description="API Call", custom_timeout=None):
    """使用优先级预检后的配置进行调用"""
    global _CACHED_ACTIVE_CONFIG, _CACHE_EXPIRES_AT
    c = get_config()
    active = get_active_config()
    if not active:
        logger.error(f"❌ {description} 无可用 AI 通路，跳过调用")
        return None
    api_key, model_name = active

    # 对齐 config_loader.py 中的字段名
    timeout = custom_timeout or c.get('timeout', 60)
    
    for attempt in range(3):
        # #13 检查 key 健康状态
        if not _is_key_healthy(api_key):
            logger.warning(f"  Key {_key_prefix(api_key)}... 不健康，尝试切换")
            _CACHE_EXPIRES_AT = 0
            active = get_active_config()
            if active:
                api_key, model_name = active
            else:
                return None

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": c.get('temperature', 0.2),
            "top_p": c.get('top_p', 0.95)
        }

        try:
            # #18 预估 token
            est_tokens = estimate_messages_tokens(messages)
            emit_event("api_call", description, {"model": model_name, "est_tokens": est_tokens, "attempt": attempt+1})

            response = requests.post(c['base_url'], headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                try:
                    result = response.json()['choices'][0]['message']['content']
                    _record_key_success(api_key)  # #13 记录成功
                    actual_tokens = len(result) // 2  # 粗略估算输出 token
                    emit_event("api_ok", description, {"output_tokens_est": actual_tokens})
                    return result
                except (KeyError, IndexError, TypeError) as e:
                    logger.error(f"❌ {description} 响应解析失败: {e} | 响应体: {response.text[:200]}")
                    emit_event("api_parse_error", description, {"error": str(e)})
                    return None
            elif response.status_code in [401, 403, 429, 500, 502, 503]:
                _record_key_failure(api_key, response.status_code)  # #13 记录失败
                logger.warning(f"⚠️ {description} 运行时通路异常 ({response.status_code})，强制重调度...")
                _CACHE_EXPIRES_AT = 0
                active = get_active_config()
                if not active:
                    logger.error(f"❌ {description} 重调度后仍无可用通路")
                    return None
                api_key, model_name = active
            else:
                logger.error(f"❌ {description} 失败: {response.status_code} | {response.text[:100]}")
        except Exception as e:
            logger.error(f"❌ {description} 连接异常: {e}")
            _CACHE_EXPIRES_AT = 0

        time.sleep(1)

    return None

def call_gemini_native(contents, model=None, tools=None, description="Native Call", custom_timeout=None):
    """Native 接口同步优先级逻辑"""
    c = get_config()
    active = get_active_config()
    if not active:
        logger.error(f"❌ {description} 无可用 AI 通路，跳过调用")
        return None
    api_key, model_name = active

    timeout = custom_timeout or c.get('timeout', 60)
    # 转换原生 URL
    native_url = c['base_url'].replace('/v1/chat/completions', f'/v1beta/models/{model_name}:generateContent')
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096}
    }
    if tools: payload["tools"] = tools

    try:
        response = requests.post(native_url, headers=headers, json=payload, timeout=timeout)
        if response.status_code == 200:
            try:
                return response.json()['candidates'][0]['content']['parts'][0].get('text', '')
            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"Gemini native 响应解析失败: {e} | 响应体: {response.text[:200]}")
                return None
    except Exception as e:
        logger.error(f"Gemini native 连接异常: {e}")
    return None

def extract_json_from_text(text):
    if not text: return None
    # 优先匹配带标识的 JSON
    match = re.search(r'```json\s*(\[.*\]|\{.*\})\s*```', text, re.DOTALL)
    if not match: match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    if match:
        try: return json.loads(match.group(1))
        except Exception: return None
    return None


# ===================== #17 Pipeline 事件总线 =====================

_EVENT_LOG = []
_EVENT_MAX = 200


def emit_event(event_type, description, metadata=None):
    """发出一个结构化事件，记录到内存日志"""
    event = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "desc": description,
    }
    if metadata:
        event["meta"] = metadata
    _EVENT_LOG.append(event)
    if len(_EVENT_LOG) > _EVENT_MAX:
        _EVENT_LOG.pop(0)
    logger.info(f"📡 [{event_type}] {description}")


def get_event_log(last_n=20):
    """获取最近 N 条事件日志"""
    return _EVENT_LOG[-last_n:]


# ===================== #18 语言感知 Token 估算 =====================

def estimate_tokens(text):
    """估算文本的 token 数量。CJK ≈ 1.5 token/字, ASCII ≈ 0.25 token/字, 消息开销 +4"""
    if not text:
        return 4
    cjk = sum(1 for c in text if '一' <= c <= '鿿' or '　' <= c <= '〿')
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - cjk - ascii_chars
    tokens = int(cjk * 1.5 + ascii_chars * 0.25 + other * 1.0 + 4)
    return tokens


def estimate_messages_tokens(messages):
    """估算 messages 数组的总 token 数"""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        total += 4  # 每条消息的固定开销
    return total


# ===================== #20 自适应流式输出 =====================

def call_ai_api_streaming(messages, description="API Call", custom_timeout=None):
    """流式调用，自动降级到非流式。返回完整文本。"""
    c = get_config()
    active = get_active_config()
    if not active:
        logger.error(f"❌ {description} 无可用 AI 通路，跳过调用")
        return None
    api_key, model_name = active
    timeout = custom_timeout or c.get('timeout', 60)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": c.get('temperature', 0.2),
        "top_p": c.get('top_p', 0.95),
        "stream": True,
    }

    try:
        response = requests.post(c['base_url'], headers=headers, json=payload, timeout=timeout, stream=True)
        if response.status_code == 200:
            full_text = ""
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    full_text += delta
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
            if full_text:
                _record_key_success(api_key)
                emit_event("streaming_ok", description, {"chars": len(full_text)})
                return full_text
        # 流式失败，降级
        logger.warning(f"⚠️ {description} 流式失败({response.status_code})，降级到非流式")
        emit_event("streaming_fallback", description, {"status": response.status_code})
        _record_key_failure(api_key, response.status_code)
    except Exception as e:
        logger.warning(f"⚠️ {description} 流式异常: {e}，降级到非流式")
        emit_event("streaming_fallback", description, {"error": str(e)})

    # 降级到非流式
    return call_ai_api(messages, description, custom_timeout)
