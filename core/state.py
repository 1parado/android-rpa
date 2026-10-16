# -*- coding: utf-8 -*-
"""断点续跑状态：logs/state.json 增量持久化 seen/greeted 集合

中断（急停 / 操作预算耗尽 / 异常 / Ctrl+C）后重跑，直接跳过已处理职位。
TTL 过期视为无效（筛选页面每天内容都在变，隔天应重扫）。
"""
import json
import time
from pathlib import Path

from core.actions import log

STATE_FILE = Path(__file__).parent.parent / "logs" / "state.json"
TTL_SECONDS = 24 * 3600   # 超过 24h 的状态自动作废


def load() -> dict:
    """读取历史状态；不存在 / 损坏 / 过期均返回空结构"""
    try:
        data = json.loads(STATE_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return _empty()
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"state.json 读取失败，忽略历史状态: {e}")
        return _empty()
    ts = data.get("updated_at", 0)
    if not isinstance(ts, (int, float)) or time.time() - ts > TTL_SECONDS:
        log.info("历史状态已过期（>24h），从头扫描")
        return _empty()
    return {
        "seen": set(data.get("seen", [])),
        "greeted": set(data.get("greeted", [])),
        "updated_at": ts,
    }


def save(state: dict) -> None:
    """全量写回（增量调用点每次只多一两条，全量写足够快）"""
    STATE_FILE.parent.mkdir(exist_ok=True)
    data = {
        "seen": sorted(state["seen"]),
        "greeted": sorted(state["greeted"]),
        "updated_at": state["updated_at"],
    }
    try:
        STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    except OSError as e:
        log.warning(f"state.json 写入失败: {e}")


def record(state: dict, title: str, greeted: bool) -> None:
    """单条处理完毕后增量记录 + 立即落盘，中断最多丢当前一条"""
    state["seen"].add(title)
    if greeted:
        state["greeted"].add(title)
    state["updated_at"] = time.time()
    save(state)


def clear() -> None:
    """正常跑完一轮后清空状态，避免无限累积"""
    try:
        STATE_FILE.unlink()
        log.info("已清理断点状态 (logs/state.json)")
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning(f"state.json 清理失败: {e}")


def _empty() -> dict:
    return {"seen": set(), "greeted": set(), "updated_at": 0.0}
